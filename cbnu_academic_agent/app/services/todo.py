from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.config import get_settings
from app.schemas import TodoItem
from app.services.change_store import find_calendar_events_for_text

logger = logging.getLogger(__name__)


class TodoPlan(BaseModel):
    todos: list[TodoItem] = Field(default_factory=list)


def breakdown_todos(goal: str, reference_date: date | None = None) -> list[TodoItem]:
    settings = get_settings()
    reference_date = reference_date or datetime.now().date()
    related_events = find_calendar_events_for_text(goal)
    if not settings.openai_api_key:
        return assign_due_dates(fallback_todos(goal), related_events, reference_date)

    llm = ChatOpenAI(model=settings.openai_model, temperature=0)
    system = SystemMessage(
        content=(
            "너는 충북대학교 학생의 학사 업무를 실행 가능한 Todo로 쪼개는 플래너다. "
            "각 Todo는 한 번에 실행 가능한 짧은 행동이어야 한다. "
            f"오늘 날짜는 {reference_date.isoformat()}이다. "
            "due_date는 오늘 날짜 이후 또는 오늘 날짜로만 지정한다. "
            "참고 일정이 있으면 그 날짜를 기준으로 due_date를 YYYY-MM-DD로 넣는다. "
            "마감/신청/제출 Todo는 관련 일정의 deadline 또는 start_date 이전으로 잡는다."
        )
    )
    human = HumanMessage(
        content=(
            f"목표 또는 일정: {goal}\n\n"
            f"캘린더 DB 참고 일정:\n{format_related_events(related_events)}"
        )
    )

    try:
        plan = llm.with_structured_output(TodoPlan).invoke([system, human])
        return assign_due_dates(plan.todos, related_events, reference_date)
    except Exception as exc:
        logger.exception("todo breakdown failed: %s", exc)
        return assign_due_dates(fallback_todos(goal), related_events, reference_date)


def fallback_todos(goal: str) -> list[TodoItem]:
    return [
        TodoItem(title=f"관련 공지 확인: {goal}", priority="high", reason="일정과 제출 조건을 먼저 확인해야 합니다."),
        TodoItem(title="필요 서류와 신청 자격 정리", priority="medium", reason="누락된 준비물을 줄이기 위한 단계입니다."),
        TodoItem(title="마감 전 제출 또는 신청 완료", priority="high", reason="학사 업무는 마감 초과 시 처리가 어려울 수 있습니다."),
    ]


def assign_due_dates(todos: list[TodoItem], related_events: list[dict], reference_date: date | None = None) -> list[TodoItem]:
    today = reference_date or datetime.now().date()
    if not related_events:
        return clear_past_due_dates(todos, today)

    primary_event = first_future_event(related_events, today)
    if not primary_event:
        return clear_past_due_dates(todos, today)

    event_date = primary_event.get("deadline") or primary_event.get("start_date") or primary_event.get("end_date")
    if not event_date:
        return clear_past_due_dates(todos, today)
    event_day = date.fromisoformat(event_date)
    last_todo_day = event_day - timedelta(days=1)
    if last_todo_day < today:
        return clear_past_due_dates(todos, today)

    schedule_dates = distribute_dates(today, last_todo_day, len(todos))

    result: list[TodoItem] = []
    for index, todo in enumerate(todos):
        if todo.due_date:
            todo_day = date.fromisoformat(todo.due_date)
            if today <= todo_day <= last_todo_day:
                result.append(todo)
            else:
                result.append(
                    todo.model_copy(
                        update={
                            "due_date": schedule_dates[index].isoformat(),
                            "reason": add_reason_context(todo.reason, primary_event, today, last_todo_day),
                        }
                    )
                )
            continue
        result.append(
            todo.model_copy(
                update={
                    "due_date": schedule_dates[index].isoformat(),
                    "reason": add_reason_context(todo.reason, primary_event, today, last_todo_day),
                }
            )
        )
    return result


def first_future_event(events: list[dict], reference_date: date | None = None) -> dict | None:
    today = reference_date or datetime.now().date()
    for event in events:
        event_date = event.get("deadline") or event.get("start_date") or event.get("end_date")
        if not event_date:
            continue
        if date.fromisoformat(event_date) > today:
            return event
    return None


def clear_past_due_dates(todos: list[TodoItem], reference_date: date) -> list[TodoItem]:
    result: list[TodoItem] = []
    for todo in todos:
        if not todo.due_date:
            result.append(todo)
            continue
        try:
            todo_day = date.fromisoformat(todo.due_date)
        except ValueError:
            result.append(todo.model_copy(update={"due_date": None}))
            continue
        if todo_day >= reference_date:
            result.append(todo)
        else:
            result.append(todo.model_copy(update={"due_date": None}))
    return result


def distribute_dates(start: date, end: date, count: int) -> list[date]:
    if count <= 0:
        return []
    if count == 1:
        return [end]

    total_days = max((end - start).days, 0)
    return [
        start + timedelta(days=round(total_days * index / (count - 1)))
        for index in range(count)
    ]


def add_reason_context(reason: str, event: dict, start: date, end: date) -> str:
    event_date = event.get("deadline") or event.get("start_date") or event.get("end_date")
    context = (
        f"캘린더 일정 '{event.get('title')}'({event_date}) 전까지의 준비 기간 "
        f"{start.isoformat()}~{end.isoformat()} 안에서 날짜를 지정했습니다."
    )
    return f"{reason} {context}".strip()


def format_related_events(events: list[dict]) -> str:
    if not events:
        return "관련 일정 없음"
    return "\n".join(
        f"- {event.get('title')} | {event.get('deadline') or event.get('start_date') or event.get('end_date')} | {event.get('category')}"
        for event in events
    )
