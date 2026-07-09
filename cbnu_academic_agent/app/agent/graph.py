from __future__ import annotations

import ast
import json
import logging
from datetime import date, datetime
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent.tools import (
    academic_rag_tool,
    cbnu_department_notice_tavily_tool,
    date_calculator_tool,
    todo_breakdown_tool,
)
from app.config import get_settings
from app.schemas import AcademicScheduleList, SourceItem
from app.services.profile_store import format_profile_context

logger = logging.getLogger(__name__)

AUTONOMOUS_TOOLS = [
    academic_rag_tool,
    cbnu_department_notice_tavily_tool,
    date_calculator_tool,
    todo_breakdown_tool,
]


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


def agent_node(state: AgentState) -> dict[str, Any]:
    """LLM이 필요한 도구를 자율적으로 선택해 tool call을 생성한다."""
    query = _latest_user_message(state)
    requested_date = date_from_request_metadata(state.get("request_metadata", {}))
    profile_context = format_profile_context(state.get("request_metadata", {}).get("profile"))
    llm = get_llm(temperature=0).bind_tools(AUTONOMOUS_TOOLS)

    system = SystemMessage(
        content=(
            "너는 충북대학교 학사 일정 관리 Agent다. 사용자의 자연어 요청을 보고 필요한 도구를 직접 선택한다. "
            "특정 학과, 학부, 전공, 단과대학 공지사항을 묻는 요청은 cbnu_department_notice_tavily_tool을 우선 호출한다. "
            "도구 호출 시 department_name에는 사용자가 말한 학과명을 넣고, 사용자가 '내 학과', '우리 학과'처럼 표현하면 사용자 프로필의 학과명을 넣는다. "
            "충북대학교 학사일정, 공지, 수강, 장학, 졸업, 등록, 시험, 휴복학 질문은 academic_rag_tool을 호출한다. "
            "특정 날짜까지 남은 기간을 묻는 요청은 date_calculator_tool을 호출한다. "
            "신청/준비/목표를 실행 가능한 할 일로 나누라는 요청은 todo_breakdown_tool을 호출한다. "
            f"Todo 도구를 호출할 때 reference_date는 반드시 {requested_date.isoformat()}로 넣는다. "
            "서비스 범위 밖 요청이면 도구를 호출하지 말고 이 서비스가 지원하는 범위를 짧게 안내한다. "
            "사용자 프로필이 있으면 도구 query나 goal에 학과, 학년, 관심 항목을 반영한다."
        )
    )
    profile_message = SystemMessage(content=f"사용자 프로필:\n{profile_context}")
    result = llm.invoke([system, profile_message, *state.get("messages", [])])
    route = infer_route_from_tool_calls(result)

    return {
        "query": query,
        "route": route,
        "route_reason": "LLM bind_tools 기반 도구 선택",
        "answer": str(result.content) if result.content else state.get("answer", ""),
        "messages": [result],
        "iterations": state.get("iterations", 0),
    }


def infer_route_from_tool_calls(message: AIMessage) -> str:
    tool_calls = getattr(message, "tool_calls", None) or []
    names = {call.get("name") for call in tool_calls}
    if "todo_breakdown_tool" in names:
        return "todo"
    if "date_calculator_tool" in names:
        return "date_calc"
    if "cbnu_department_notice_tavily_tool" in names:
        return "academic_rag"
    if "academic_rag_tool" in names:
        return "academic_rag"
    return "guardrail"


def extract_schedule_node(state: AgentState) -> dict[str, Any]:
    """OutputParser를 활용해 검색 문맥에서 일정 JSON을 추출한다."""
    context_docs = state.get("context_docs", [])
    if not context_docs:
        return {"schedules": []}

    context_text = "\n\n".join(
        f"[출처 {idx + 1}] {doc.get('title')}\n"
        f"게시일 추정: {doc.get('published_date') or '알 수 없음'}\n"
        f"URL: {doc.get('source')}\n"
        f"본문: {doc.get('content', '')[:1200]}"
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
        f"[문서 {idx + 1}] {doc.get('title')}\n"
        f"게시일 추정: {doc.get('published_date') or '알 수 없음'}\n"
        f"URL: {doc.get('source')}\n"
        f"{doc.get('content', '')[:1400]}"
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
                "공지사항 검색 결과는 게시일 추정 값이 있으면 최신순으로 정리한다. "
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


def process_tool_results_node(state: AgentState) -> dict[str, Any]:
    """ToolNode 결과를 앱 응답 상태로 변환한다."""
    requested_date = date_from_request_metadata(state.get("request_metadata", {}))
    updates: dict[str, Any] = {}

    for message in reversed(state.get("messages", [])):
        if not isinstance(message, ToolMessage):
            continue

        tool_name = message.name
        payload = parse_tool_payload(message.content)

        if tool_name in {"academic_rag_tool", "cbnu_department_notice_tavily_tool"}:
            docs = payload if isinstance(payload, list) else []
            updates["route"] = "academic_rag"
            updates["context_docs"] = docs
            continue

        if tool_name == "date_calculator_tool":
            content = str(message.content)
            updates["route"] = "date_calc"
            updates["answer"] = content
            updates["messages"] = [AIMessage(content=content)]
            continue

        if tool_name == "todo_breakdown_tool":
            todos = payload if isinstance(payload, list) else []
            todos = filter_todos_from_date(todos, requested_date)
            content = format_todo_answer(todos, requested_date)
            updates["route"] = "todo"
            updates["todos"] = todos
            updates["answer"] = content
            updates["messages"] = [AIMessage(content=content)]
            continue

    if not updates:
        updates["route"] = "guardrail"
        updates["answer"] = latest_ai_content(state)
    return updates


def parse_tool_payload(content: Any) -> Any:
    if not isinstance(content, str):
        return content
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(content)
    except (ValueError, SyntaxError):
        return content


def latest_ai_content(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, AIMessage) and message.content:
            return str(message.content)
    return "응답을 생성하지 못했습니다."


def route_after_tool_processing(state: AgentState) -> str:
    if state.get("route") == "academic_rag":
        return "extract_schedule"
    return "end"


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(AUTONOMOUS_TOOLS))
    graph.add_node("process_tool_results", process_tool_results_node)
    graph.add_node("extract_schedule", extract_schedule_node)
    graph.add_node("answer", answer_node)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        tools_condition,
        {
            "tools": "tools",
            END: END,
        },
    )
    graph.add_edge("tools", "process_tool_results")
    graph.add_conditional_edges(
        "process_tool_results",
        route_after_tool_processing,
        {
            "extract_schedule": "extract_schedule",
            "end": END,
        },
    )
    graph.add_edge("extract_schedule", "answer")
    graph.add_edge("answer", END)

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
