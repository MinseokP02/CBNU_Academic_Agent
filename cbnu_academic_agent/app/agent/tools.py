from __future__ import annotations

import re
from datetime import date
from typing import Any

from langchain_core.documents import Document
from langchain_core.tools import tool

from app.config import get_settings
from app.services.crawler import crawl_realtime_sources
from app.services.date_utils import days_until
from app.services.rag import retrieve_with_hybrid_vectorstore
from app.services.todo import breakdown_todos
from app.services.vector_db import index_academic_documents


@tool
def realtime_cbnu_crawl_tool(query: str) -> list[dict[str, Any]]:
    """충북대학교 공식 페이지를 실시간 크롤링해 관련 문서 후보를 가져온다."""
    settings = get_settings()
    docs = crawl_realtime_sources(
        query=query,
        seed_urls=settings.default_sources,
        timeout=settings.crawl_timeout,
        max_links=settings.max_links,
    )
    return [
        {
            "title": doc.metadata.get("title", "제목 없음"),
            "source": doc.metadata.get("source", ""),
            "content": doc.page_content,
        }
        for doc in docs
    ]


@tool
def runtime_rag_search_tool(query: str, documents: list[dict[str, Any]], k: int = 5) -> list[dict[str, Any]]:
    """영구 Chroma DB와 실시간 크롤링 문서를 함께 검색한다."""
    docs = [
        Document(
            page_content=item.get("content", ""),
            metadata={"title": item.get("title", "제목 없음"), "source": item.get("source", "")},
        )
        for item in documents
        if item.get("content")
    ]
    index_academic_documents(docs)
    results = retrieve_with_hybrid_vectorstore(query=query, docs=docs, k=k)
    return [
        {
            "title": doc.metadata.get("title", "제목 없음"),
            "source": doc.metadata.get("source", ""),
            "content": doc.page_content,
        }
        for doc in results
    ]


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


def find_first_date(text: str) -> str | None:
    match = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", text)
    if not match:
        return None
    year, month, day = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
