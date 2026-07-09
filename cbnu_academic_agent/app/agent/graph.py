from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph, add_messages

from app.agent.tools import (
    date_calculator_tool,
    find_first_date,
    realtime_cbnu_crawl_tool,
    runtime_rag_search_tool,
    todo_breakdown_tool,
)
from app.config import get_settings
from app.schemas import AcademicScheduleList, RouteDecision, SourceItem
from app.services.profile_store import format_profile_context

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    query: str
    rewritten_query: str
    route: Literal["academic_rag", "date_calc", "todo", "guardrail"]
    route_reason: str
    raw_docs: list[dict[str, Any]]
    context_docs: list[dict[str, Any]]
    schedules: list[dict[str, Any]]
    todos: list[dict[str, Any]]
    answer: str
    iterations: int
    request_metadata: dict[str, Any]


def get_llm(temperature: float = 0.1) -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(model=settings.openai_model, temperature=temperature)


def _latest_user_message(state: AgentState) -> str:
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            return str(msg.content)
    return state.get("query", "")


def classify_request(state: AgentState) -> dict[str, Any]:
    """사용자 요청을 보고 어떤 도구 흐름을 탈지 결정한다."""
    query = _latest_user_message(state)
    llm = get_llm(temperature=0)
    profile_context = format_profile_context(state.get("request_metadata", {}).get("profile"))

    system = SystemMessage(
        content=(
            "너는 충북대학교 학사/공지/일정 관리 Agent의 라우터다. "
            "사용자 요청을 academic_rag, date_calc, todo, guardrail 중 하나로 분류한다. "
            "academic_rag는 충북대학교 학사일정, 공지, 수강신청, 장학, 졸업, 등록, 시험, 휴복학 질문이다. "
            "date_calc는 사용자가 명시한 날짜까지 며칠 남았는지 계산하는 요청이다. "
            "todo는 학사 일정, 신청, 준비 작업을 실행 가능한 할 일 목록으로 분해해 달라는 요청이다. "
            "guardrail은 서비스 범위 밖 요청이다. 검색어는 한국어로 간결하게 재작성한다."
        )
    )
    profile_message = SystemMessage(content=f"사용자 프로필:\n{profile_context}")

    try:
        decision = llm.with_structured_output(RouteDecision).invoke(
            [system, profile_message, *state.get("messages", [])]
        )
    except Exception as exc:  # API 일시 오류 시 키워드 기반 fallback
        logger.exception("route classification failed: %s", exc)
        academic_keywords = ["충북", "학사", "공지", "수강", "장학", "졸업", "등록", "시험", "휴학", "복학", "일정"]
        if any(k in query for k in academic_keywords):
            decision = RouteDecision(route="academic_rag", reason="키워드 기반 fallback", rewritten_query=query)
        elif find_first_date(query):
            decision = RouteDecision(route="date_calc", reason="날짜 표현 감지 fallback", rewritten_query=query)
        elif any(k in query for k in ["todo", "할 일", "체크리스트", "쪼개", "분해"]):
            decision = RouteDecision(route="todo", reason="Todo 요청 fallback", rewritten_query=query)
        else:
            decision = RouteDecision(route="guardrail", reason="서비스 범위 외 fallback", rewritten_query=query)

    return {
        "query": query,
        "route": decision.route,
        "route_reason": decision.reason,
        "rewritten_query": decision.rewritten_query,
        "iterations": state.get("iterations", 0),
    }


def route_condition(state: AgentState) -> str:
    return state.get("route", "guardrail")


def crawl_node(state: AgentState) -> dict[str, Any]:
    query = state.get("rewritten_query") or state.get("query") or _latest_user_message(state)
    profile_context = format_profile_context(state.get("request_metadata", {}).get("profile"))
    if profile_context != "설정된 사용자 프로필 없음":
        query = f"{query}\n{profile_context}"
    docs = realtime_cbnu_crawl_tool.invoke({"query": query})
    return {"raw_docs": docs, "iterations": state.get("iterations", 0) + 1}


def rag_search_node(state: AgentState) -> dict[str, Any]:
    query = state.get("rewritten_query") or state.get("query") or _latest_user_message(state)
    profile_context = format_profile_context(state.get("request_metadata", {}).get("profile"))
    if profile_context != "설정된 사용자 프로필 없음":
        query = f"{query}\n{profile_context}"
    docs = state.get("raw_docs", [])
    results = runtime_rag_search_tool.invoke({"query": query, "documents": docs, "k": 5})
    return {"context_docs": results}


def should_retry_search(state: AgentState) -> str:
    if state.get("context_docs"):
        return "extract"
    if state.get("iterations", 0) < 2:
        return "retry"
    return "extract"


def expand_query_node(state: AgentState) -> dict[str, Any]:
    original = state.get("query") or _latest_user_message(state)
    return {
        "rewritten_query": f"충북대학교 학사일정 공지 수강 장학 졸업 등록 {original}",
    }


def extract_schedule_node(state: AgentState) -> dict[str, Any]:
    """OutputParser를 활용해 검색 문맥에서 일정 JSON을 추출한다."""
    context_docs = state.get("context_docs", [])
    if not context_docs:
        return {"schedules": []}

    context_text = "\n\n".join(
        f"[출처 {idx + 1}] {doc.get('title')}\nURL: {doc.get('source')}\n본문: {doc.get('content', '')[:1200]}"
        for idx, doc in enumerate(context_docs)
    )

    parser = PydanticOutputParser(pydantic_object=AcademicScheduleList)
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "너는 충북대학교 공지/학사일정에서 일정 정보를 추출하는 파서다. "
                "본문에 명확한 날짜가 있는 일정만 추출한다. 날짜는 YYYY-MM-DD로 변환한다. "
                "연도가 없으면 현재 검색 문맥의 연도나 오늘 기준으로 가장 합리적인 연도를 사용하되, 불확실하면 null로 둔다. "
                "반드시 지정된 JSON 스키마만 출력한다.\n{format_instructions}",
            ),
            ("human", "사용자 질문: {query}\n\n검색 문맥:\n{context}"),
        ]
    )

    chain = prompt | get_llm(temperature=0) | parser
    try:
        parsed: AcademicScheduleList = chain.invoke(
            {
                "query": state.get("query", ""),
                "context": context_text,
                "format_instructions": parser.get_format_instructions(),
            }
        )
        return {"schedules": [item.model_dump() for item in parsed.schedules]}
    except Exception as exc:
        logger.exception("schedule parsing failed: %s", exc)
        return {"schedules": []}


def answer_node(state: AgentState) -> dict[str, Any]:
    context_docs = state.get("context_docs", [])
    schedules = state.get("schedules", [])
    query = state.get("query") or _latest_user_message(state)
    profile_context = format_profile_context(state.get("request_metadata", {}).get("profile"))

    context_text = "\n\n".join(
        f"[문서 {idx + 1}] {doc.get('title')}\nURL: {doc.get('source')}\n{doc.get('content', '')[:1400]}"
        for idx, doc in enumerate(context_docs)
    )

    schedule_json = json.dumps(schedules, ensure_ascii=False, indent=2)
    llm = get_llm(temperature=0.2)
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "너는 충북대학교 학생을 위한 학사 일정 관리 Agent다. "
                "검색 문맥에 근거해서만 답하고, 불확실하면 불확실하다고 말한다. "
                "사용자 프로필이 있으면 학과, 학년, 관심 항목과 관련성이 높은 내용을 우선해서 답한다. "
                "중요 일정은 날짜, 마감일, 해야 할 일을 중심으로 정리한다. "
                "마지막에는 확인한 출처 제목을 짧게 제시한다."
            ),
            (
                "human",
                "사용자 프로필:\n{profile_context}\n\n사용자 질문: {query}\n\n추출된 일정 JSON:\n{schedule_json}\n\n검색 문맥:\n{context}\n\n답변:",
            ),
        ]
    )
    result = (prompt | llm).invoke(
        {
            "profile_context": profile_context,
            "query": query,
            "schedule_json": schedule_json,
            "context": context_text,
        }
    )
    content = str(result.content)
    return {"answer": content, "messages": [AIMessage(content=content)]}


def date_calc_node(state: AgentState) -> dict[str, Any]:
    query = state.get("query") or _latest_user_message(state)
    date_text = find_first_date(query)
    if not date_text:
        content = "계산할 날짜를 YYYY-MM-DD 형식으로 함께 입력해 주세요. 예: 2026-08-05까지 며칠 남았어?"
    else:
        content = date_calculator_tool.invoke({"date_text": date_text})
    return {"answer": content, "messages": [AIMessage(content=content)]}


def guardrail_node(state: AgentState) -> dict[str, Any]:
    content = (
        "이 Agent는 충북대학교 학사 일정, 공지, 수강신청, 장학, 졸업, 등록 관련 질문을 돕기 위한 서비스입니다. "
        "예를 들어 ‘이번 달 학사일정 알려줘’, ‘수강신청 공지 찾아줘’, ‘장학금 신청 마감일 정리해줘’처럼 질문해 주세요."
    )
    return {"answer": content, "messages": [AIMessage(content=content)]}


def todo_node(state: AgentState) -> dict[str, Any]:
    query = state.get("query") or _latest_user_message(state)
    requested_date = date_from_request_metadata(state.get("request_metadata", {}))
    profile_context = format_profile_context(state.get("request_metadata", {}).get("profile"))
    goal = query if profile_context == "설정된 사용자 프로필 없음" else f"{query}\n\n사용자 프로필:\n{profile_context}"
    todos = todo_breakdown_tool.invoke({"goal": goal, "reference_date": requested_date.isoformat()})
    todos = filter_todos_from_date(todos, requested_date)
    content = format_todo_answer(todos, requested_date)
    return {"answer": content, "todos": todos, "messages": [AIMessage(content=content)]}


def format_todo_answer(todos: list[dict[str, Any]], requested_date: date) -> str:
    if not todos:
        return (
            f"{requested_date.isoformat()} 이후로 표시할 Todo 날짜를 찾지 못했습니다. "
            "먼저 관련 학사 일정/공지 동기화를 하거나, 목표에 마감일을 함께 입력해 주세요."
        )

    lines = [f"{requested_date.isoformat()} 이후 기준으로 다음 순서로 처리하면 좋습니다."]
    for idx, todo in enumerate(todos, start=1):
        due_date = todo.get("due_date") or "날짜 미정"
        lines.append(f"{idx}. {todo.get('title')} ({due_date}, {todo.get('priority')})")
        if todo.get("reason"):
            lines.append(f"   - {todo.get('reason')}")
    return "\n".join(lines)


def date_from_request_metadata(metadata: dict[str, Any] | None) -> date:
    requested_at = (metadata or {}).get("requested_at")
    if not requested_at:
        return date.today()
    try:
        return datetime.fromisoformat(str(requested_at)).date()
    except ValueError:
        return date.today()


def filter_todos_from_date(todos: list[dict[str, Any]], requested_date: date) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for todo in todos:
        due_date = todo.get("due_date")
        if not due_date:
            continue
        try:
            due_day = date.fromisoformat(str(due_date))
        except ValueError:
            continue
        if due_day >= requested_date:
            filtered.append(todo)
    return filtered


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("classify_request", classify_request)
    graph.add_node("crawl_realtime_web", crawl_node)
    graph.add_node("rag_search", rag_search_node)
    graph.add_node("expand_query", expand_query_node)
    graph.add_node("extract_schedule", extract_schedule_node)
    graph.add_node("answer", answer_node)
    graph.add_node("date_calc", date_calc_node)
    graph.add_node("todo", todo_node)
    graph.add_node("guardrail", guardrail_node)

    graph.add_edge(START, "classify_request")
    graph.add_conditional_edges(
        "classify_request",
        route_condition,
        {
            "academic_rag": "crawl_realtime_web",
            "date_calc": "date_calc",
            "todo": "todo",
            "guardrail": "guardrail",
        },
    )
    graph.add_edge("crawl_realtime_web", "rag_search")
    graph.add_conditional_edges(
        "rag_search",
        should_retry_search,
        {
            "retry": "expand_query",
            "extract": "extract_schedule",
        },
    )
    graph.add_edge("expand_query", "crawl_realtime_web")
    graph.add_edge("extract_schedule", "answer")
    graph.add_edge("answer", END)
    graph.add_edge("date_calc", END)
    graph.add_edge("todo", END)
    graph.add_edge("guardrail", END)

    memory = InMemorySaver()
    return graph.compile(checkpointer=memory)


agent_graph = build_graph()


def invoke_agent(
    message: str,
    session_id: str = "default",
    request_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = {"configurable": {"thread_id": session_id}}
    result = agent_graph.invoke(
        {
            "messages": [HumanMessage(content=message)],
            "query": message,
            "iterations": 0,
            "request_metadata": request_metadata or {},
        },
        config=config,
    )

    sources = []
    for doc in result.get("context_docs", [])[:5]:
        sources.append(
            SourceItem(
                title=doc.get("title", "제목 없음"),
                url=doc.get("source", ""),
                snippet=doc.get("content", "")[:180],
            ).model_dump()
        )

    return {
        "answer": result.get("answer", "응답을 생성하지 못했습니다."),
        "session_id": session_id,
        "route": result.get("route", "unknown"),
        "sources": sources,
        "schedules": result.get("schedules", []),
        "todos": result.get("todos", []),
        "calendar_events": [],
    }
