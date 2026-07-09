from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from langchain_core.documents import Document
from langchain_core.tools import tool

from app.config import get_settings
from app.services.crawler import crawl_realtime_sources
from app.services.date_utils import days_until
from app.services.rag import retrieve_with_hybrid_vectorstore
from app.services.todo import breakdown_todos
from app.services.vector_db import index_academic_documents


@tool
def academic_rag_tool(query: str, k: int = 5) -> list[dict[str, Any]]:
    """충북대학교 학사/공지 질문에 대해 실시간 크롤링과 Chroma RAG 검색을 함께 수행한다."""
    settings = get_settings()
    crawled_docs = crawl_realtime_sources(
        query=query,
        seed_urls=settings.default_sources,
        timeout=settings.crawl_timeout,
        max_links=settings.max_links,
    )
    index_academic_documents(crawled_docs)
    results = retrieve_with_hybrid_vectorstore(query=query, docs=crawled_docs, k=k)
    return [
        {
            "title": doc.metadata.get("title", "제목 없음"),
            "source": doc.metadata.get("source", ""),
            "content": doc.page_content,
        }
        for doc in results
    ]


@tool
def cbnu_department_notice_tavily_tool(
    department_name: str,
    query: str,
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """Tavily로 충북대학교 특정 학과 공지사항을 검색하고 결과를 Chroma에 저장한다."""
    settings = get_settings()
    if not settings.tavily_api_key:
        return [
            {
                "title": "Tavily API Key가 설정되지 않았습니다.",
                "source": "",
                "content": ".env에 TAVILY_API_KEY를 설정한 뒤 다시 요청하세요.",
            }
        ]

    today = date.today()
    search_query = (
        f"충북대학교 \"{department_name}\" 공지사항 최신 최근 {today.year} "
        f"{query}"
    ).strip()
    fetch_count = max(5, min(max_results * 2, 10))
    payload = {
        "query": search_query,
        "search_depth": "basic",
        "max_results": fetch_count,
        "topic": "general",
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "include_domains": settings.cbnu_department_notice_domains,
        "exclude_domains": [],
        "safe_search": False,
    }
    headers = {
        "Authorization": f"Bearer {settings.tavily_api_key}",
        "Content-Type": "application/json",
    }

    try:
        data = tavily_search(settings.tavily_search_url, payload, headers, settings.crawl_timeout)
        filtered_results = filter_department_notice_results(data.get("results", []), department_name)
        if not filtered_results:
            fallback_payload = dict(payload)
            fallback_payload["include_domains"] = []
            data = tavily_search(settings.tavily_search_url, fallback_payload, headers, settings.crawl_timeout)
            filtered_results = filter_department_notice_results(data.get("results", []), department_name)
    except httpx.HTTPError as exc:
        return [
            {
                "title": "Tavily 검색 실패",
                "source": "",
                "content": f"Tavily API 호출 중 오류가 발생했습니다: {exc}",
            }
        ]

    docs = []
    sorted_results = sorted(
        filtered_results,
        key=tavily_notice_sort_key,
        reverse=True,
    )[: max(1, min(max_results, 10))]
    for item in sorted_results:
        title = item.get("title") or f"충북대학교 {department_name} 공지"
        source = item.get("url") or ""
        content = item.get("content") or item.get("raw_content") or ""
        if not content:
            continue
        notice_date = extract_notice_date(item)
        dated_content = f"[게시일 추정: {notice_date}]\n{content}" if notice_date else content
        docs.append(
            Document(
                page_content=dated_content,
                metadata={
                    "title": title,
                    "source": source,
                    "kind": "cbnu_department_notice_tavily",
                    "department_name": department_name,
                    "published_date": notice_date,
                    "score": item.get("score"),
                },
            )
        )

    if docs:
        index_academic_documents(docs)

    if not docs:
        return [
            {
                "title": f"{department_name} 공지사항 검색 결과 없음",
                "source": "",
                "content": (
                    f"Tavily에서 '{department_name}'과 직접 관련된 공지사항을 찾지 못했습니다. "
                    "CBNU_DEPARTMENT_NOTICE_DOMAINS에 해당 학과 사이트 도메인을 추가하면 정확도가 올라갑니다."
                ),
                "published_date": None,
                "score": 0,
            }
        ]

    return [
        {
            "title": doc.metadata.get("title", "제목 없음"),
            "source": doc.metadata.get("source", ""),
            "content": doc.page_content,
            "published_date": doc.metadata.get("published_date"),
            "score": doc.metadata.get("score"),
        }
        for doc in docs
    ]


def tavily_search(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def filter_department_notice_results(results: list[dict[str, Any]], department_name: str) -> list[dict[str, Any]]:
    return [
        item for item in results
        if is_department_notice_result(item, department_name)
    ]


def is_department_notice_result(item: dict[str, Any], department_name: str) -> bool:
    title = str(item.get("title") or "")
    url = str(item.get("url") or "")
    content = str(item.get("content") or item.get("raw_content") or "")
    combined = f"{title} {url} {content}".lower()

    if "통합검색" in title or "selectsearch" in url.lower() or "search" in url.lower():
        return False
    if re.search(r"[?&]q=\s*(&|$)", url):
        return False
    if is_download_file_url(url):
        return False

    if not is_cbnu_related_url(url):
        return False

    department_tokens = department_search_tokens(department_name)
    has_department = any(token.lower() in combined for token in department_tokens)
    has_notice = any(word in combined for word in ("공지", "notice", "board", "bbs"))
    return has_department and has_notice


def is_cbnu_related_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host.endswith("cbnu.ac.kr") or host.endswith("chungbuk.ac.kr")


def is_download_file_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith((".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip"))


def department_search_tokens(department_name: str) -> list[str]:
    normalized = department_name.strip()
    tokens = {normalized}
    suffixes = ("학과", "학부", "전공", "대학", "대학원")
    for suffix in suffixes:
        if normalized.endswith(suffix):
            tokens.add(normalized[: -len(suffix)])
    if "컴퓨터" in normalized:
        tokens.update({"컴퓨터", "소프트웨어", "소프트웨어학부", "software"})
    tokens = {token for token in tokens if len(token) >= 2}
    return sorted(tokens, key=len, reverse=True)


def tavily_notice_sort_key(item: dict[str, Any]) -> tuple[str, float]:
    notice_date = extract_notice_date(item) or "0000-00-00"
    score = item.get("score") or 0
    try:
        score_value = float(score)
    except (TypeError, ValueError):
        score_value = 0.0
    return notice_date, score_value


def extract_notice_date(item: dict[str, Any]) -> str | None:
    for key in ("published_date", "publishedDate", "date", "crawl_date", "last_updated"):
        parsed = normalize_date_text(item.get(key))
        if parsed:
            return parsed

    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("title", "url", "content", "raw_content")
    )
    return find_latest_date_in_text(haystack)


def normalize_date_text(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    iso_match = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", text)
    if iso_match:
        year, month, day = iso_match.groups()
        return safe_iso_date(year, month, day)

    korean_match = re.search(r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일", text)
    if korean_match:
        year, month, day = korean_match.groups()
        return safe_iso_date(year, month, day)

    inferred_match = re.search(r"(20\d{2}).{0,40}?(\d{1,2})[./월]\s*(\d{1,2})", text)
    if inferred_match:
        year, month, day = inferred_match.groups()
        return safe_iso_date(year, month, day)

    compact_match = re.search(r"(20\d{2})(\d{2})(\d{2})", text)
    if compact_match:
        year, month, day = compact_match.groups()
        return safe_iso_date(year, month, day)

    year_match = re.search(r"(20\d{2})", text)
    if year_match:
        return safe_iso_date(year_match.group(1), "1", "1")

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def find_latest_date_in_text(text: str) -> str | None:
    dates = []
    patterns = [
        r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})",
        r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일",
        r"(20\d{2}).{0,40}?(\d{1,2})[./월]\s*(\d{1,2})",
        r"(20\d{2})(\d{2})(\d{2})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            parsed = safe_iso_date(*match.groups())
            if parsed:
                dates.append(parsed)
    return max(dates) if dates else None


def safe_iso_date(year: str, month: str, day: str) -> str | None:
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


@tool
def date_calculator_tool(date_text: str) -> str:
    """YYYY-MM-DD 또는 YYYY.MM.DD 날짜까지 남은 기간을 계산한다."""
    return days_until(date_text)


@tool
def todo_breakdown_tool(goal: str, reference_date: str | None = None) -> list[dict[str, str | None]]:
    """학사 일정이나 목표를 실행 가능한 Todo 목록으로 분해한다."""
    parsed_date = None
    if reference_date:
        try:
            parsed_date = date.fromisoformat(reference_date)
        except ValueError:
            parsed_date = None
    return [todo.model_dump() for todo in breakdown_todos(goal, reference_date=parsed_date)]


