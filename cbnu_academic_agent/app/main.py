from __future__ import annotations

import logging
import shutil
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.agent.graph import agent_graph, invoke_agent
from app.config import get_settings
from app.middleware.logging import RequestLoggingMiddleware
from app.schemas import (
    CalendarEvent,
    ChangeItem,
    ChatRequest,
    ChatResponse,
    CrawlSyncResponse,
    ProfileResponse,
    ProfileSettings,
    TodoBreakdownRequest,
    TodoBreakdownResponse,
    UploadResponse,
)
from app.services.change_store import (
    connect,
    detect_and_store_changes,
    insert_calendar_event,
    list_calendar_events,
    list_changes,
)
from app.services.academic_schedule import load_academic_schedule_range
from app.services.crawler import crawl_realtime_sources
from app.services.profile_store import get_profile, upsert_profile
from app.services.todo import breakdown_todos
from app.services.vector_db import PROFILE_COLLECTION, index_academic_documents, index_profile_pdf

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(RequestLoggingMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": settings.app_name}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request):
    if not settings.openai_api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY가 설정되어 있지 않습니다. .env를 확인하세요.")

    try:
        agent_context = getattr(request.state, "agent_context", {})
        profile = get_profile(req.session_id)
        agent_context = {**agent_context, "profile": profile.model_dump()}
        result = invoke_agent(
            message=req.message,
            session_id=req.session_id,
            request_metadata=agent_context,
        )
        store_schedules_to_calendar(result.get("schedules", []))
        if result.get("route") == "todo":
            requested_date = date_from_request_metadata(agent_context)
            result["todos"] = filter_todos_from_date(result.get("todos", []), requested_date)
            result["calendar_events"] = store_todos_to_calendar(
                result.get("todos", []),
                req.message,
                request_id=agent_context.get("request_id"),
                requested_date=requested_date,
            )
        return ChatResponse(**result)
    except Exception as exc:
        logging.getLogger(__name__).exception("chat failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Agent 실행 중 오류가 발생했습니다.",
                "hint": "API Key, 네트워크, 크롤링 대상 URL, 패키지 버전을 확인하세요.",
            },
        )


@app.get("/api/graph/mermaid", response_class=PlainTextResponse)
def graph_mermaid() -> str:
    return agent_graph.get_graph().draw_mermaid()


@app.get("/api/sources")
def sources():
    return {"sources": settings.default_sources}


@app.get("/api/profile/{session_id}", response_model=ProfileResponse)
def read_profile(session_id: str):
    return ProfileResponse(profile=get_profile(session_id))


@app.post("/api/profile", response_model=ProfileResponse)
def save_profile(profile: ProfileSettings):
    saved = upsert_profile(profile)
    return ProfileResponse(profile=saved, message="프로필 설정을 저장했습니다.")


@app.post("/api/profile/upload", response_model=UploadResponse)
def upload_profile_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 업로드할 수 있습니다.")

    safe_name = Path(file.filename).name
    target_path = settings.upload_dir / safe_name
    with target_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    chunks = index_profile_pdf(target_path, safe_name)
    return UploadResponse(
        filename=safe_name,
        chunks=chunks,
        collection=PROFILE_COLLECTION,
        message="사용자 프로필 PDF를 Chroma에 저장했습니다.",
    )


@app.post("/api/crawl/sync", response_model=CrawlSyncResponse)
def sync_crawl_to_chroma():
    schedule_events, schedule_docs = load_academic_schedule_range(
        settings.academic_schedule_start,
        settings.academic_schedule_end,
    )
    docs = crawl_realtime_sources(
        query="충북대학교 학사 일정 공지 수강 장학 등록 졸업 시험",
        seed_urls=settings.default_sources,
        timeout=settings.crawl_timeout,
        max_links=settings.max_links,
    )
    indexed_count = index_academic_documents([*schedule_docs, *docs])
    stored_schedule_events = store_events_to_calendar(schedule_events)
    change_result = detect_and_store_changes(docs)
    stats = change_result["stats"]
    return CrawlSyncResponse(
        indexed_documents=indexed_count,
        new_count=stats["new"],
        changed_count=stats["changed"],
        unchanged_count=stats["unchanged"],
        calendar_events=[*stored_schedule_events, *change_result["events"]],
    )


@app.get("/api/calendar", response_model=list[CalendarEvent])
def calendar_events():
    return list_calendar_events(settings.academic_schedule_start, settings.academic_schedule_end)


@app.get("/api/changes", response_model=list[ChangeItem])
def changes():
    return list_changes()


@app.post("/api/todos/breakdown", response_model=TodoBreakdownResponse)
def todos_breakdown(req: TodoBreakdownRequest):
    requested_date = date.today()
    todos = filter_todos_from_date(breakdown_todos(req.goal, reference_date=requested_date), requested_date)
    calendar_events = store_todos_to_calendar(todos, req.goal, requested_date=requested_date)
    return TodoBreakdownResponse(todos=todos, calendar_events=calendar_events)


def store_schedules_to_calendar(schedules: list[dict]) -> None:
    if not schedules:
        return
    with connect() as conn:
        for schedule in schedules:
            insert_calendar_event(
                conn,
                {
                    "title": schedule.get("title", "학사 일정"),
                    "category": schedule.get("category", "기타"),
                    "start_date": schedule.get("start_date"),
                    "end_date": schedule.get("end_date"),
                    "deadline": schedule.get("deadline"),
                    "importance": schedule.get("importance", "medium"),
                    "source_url": schedule.get("source_url"),
                    "evidence": schedule.get("evidence", ""),
                    "change_type": "chat_extract",
                },
            )
        conn.commit()


def store_events_to_calendar(events: list[dict]) -> list[dict]:
    stored_events: list[dict] = []
    if not events:
        return stored_events

    with connect() as conn:
        for event in events:
            event_id = insert_calendar_event(conn, event)
            if event_id:
                event = dict(event)
                event["id"] = event_id
                stored_events.append(event)
        conn.commit()

    return stored_events


def store_todos_to_calendar(
    todos: list,
    goal: str,
    request_id: str | None = None,
    requested_date: date | None = None,
) -> list[dict]:
    stored_events: list[dict] = []
    requested_date = requested_date or date.today()
    with connect() as conn:
        for todo in todos:
            due_date = todo_value(todo, "due_date")
            if not due_date:
                continue
            try:
                due_day = date.fromisoformat(due_date)
            except ValueError:
                continue
            if due_day < requested_date:
                continue
            title = todo_value(todo, "title") or "할 일"
            priority = todo_value(todo, "priority") or "medium"
            reason = todo_value(todo, "reason") or ""
            evidence = f"{goal} - {reason}"
            if request_id:
                evidence = f"[request:{request_id}] {evidence}"
            event = {
                "title": f"TODO: {title}",
                "category": "Todo",
                "start_date": due_date,
                "end_date": None,
                "deadline": due_date,
                "importance": priority,
                "source_url": None,
                "evidence": evidence,
                "change_type": "todo",
            }
            event_id = insert_calendar_event(conn, event)
            if not event_id:
                existing = conn.execute(
                    """
                    SELECT id FROM calendar_events
                    WHERE title = ?
                      AND COALESCE(source_url, '') = ''
                      AND COALESCE(deadline, start_date, end_date, '') = ?
                    LIMIT 1
                    """,
                    (event["title"], due_date),
                ).fetchone()
                event_id = int(existing["id"]) if existing else 0
            if event_id:
                stored_events.append({**event, "id": event_id})
        conn.commit()
    return stored_events


def todo_value(todo, key: str):
    if isinstance(todo, dict):
        return todo.get(key)
    return getattr(todo, key, None)


def date_from_request_metadata(metadata: dict | None) -> date:
    requested_at = (metadata or {}).get("requested_at")
    if not requested_at:
        return date.today()
    try:
        return datetime.fromisoformat(requested_at).date()
    except ValueError:
        return date.today()


def filter_todos_from_date(todos: list, requested_date: date) -> list:
    filtered = []
    for todo in todos:
        due_date = todo_value(todo, "due_date")
        if not due_date:
            continue
        try:
            due_day = date.fromisoformat(due_date)
        except ValueError:
            continue
        if due_day >= requested_date:
            filtered.append(todo)
    return filtered
