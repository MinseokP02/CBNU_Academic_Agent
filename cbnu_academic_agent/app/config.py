from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from datetime import date, datetime
from typing import List

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def parse_source_urls(value: str | None) -> list[str]:
    if not value:
        return []
    return [url.strip() for url in value.split(",") if url.strip()]


class Settings(BaseModel):
    app_name: str = "CBNU Academic Planner Agent"
    app_env: str = Field(default_factory=lambda: os.getenv("APP_ENV", "dev"))
    log_level: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    openai_api_key: str | None = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    openai_model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    openai_embedding_model: str = Field(
        default_factory=lambda: os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    )
    tavily_api_key: str | None = Field(default_factory=lambda: os.getenv("TAVILY_API_KEY"))
    tavily_search_url: str = Field(default_factory=lambda: os.getenv("TAVILY_SEARCH_URL", "https://api.tavily.com/search"))
    cbnu_department_notice_domains: List[str] = Field(
        default_factory=lambda: parse_source_urls(
            os.getenv("CBNU_DEPARTMENT_NOTICE_DOMAINS", "cbnu.ac.kr,chungbuk.ac.kr,software.cbnu.ac.kr,computer.chungbuk.ac.kr")
        )
    )

    crawl_timeout: int = Field(default_factory=lambda: int(os.getenv("CBNU_CRAWL_TIMEOUT", "10")))
    max_links: int = Field(default_factory=lambda: int(os.getenv("CBNU_MAX_LINKS", "40")))
    current_year: int = Field(default_factory=lambda: datetime.now().year)

    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[1])
    data_dir: Path = Field(default_factory=lambda: Path(os.getenv("CBNU_DATA_DIR", "data")))
    upload_dir: Path = Field(default_factory=lambda: Path(os.getenv("CBNU_UPLOAD_DIR", "data/uploads")))
    chroma_dir: Path = Field(default_factory=lambda: Path(os.getenv("CBNU_CHROMA_DIR", "data/chroma")))
    sqlite_path: Path = Field(default_factory=lambda: Path(os.getenv("CBNU_SQLITE_PATH", "data/cbnu_agent.db")))

    # 충북대학교 공식/관련 페이지.
    core_sources: List[str] = [
        "https://www.cbnu.ac.kr/www/index.do",
        "https://www.cbnu.ac.kr/www/selectWebSchdulList.do?key=455&schdulSeNo=1",
        "https://www.cbnu.ac.kr/www/selectBbsNttList.do?bbsNo=8&key=813",
    ]

    # 충북대학교 공식 단과대학/학과군 페이지. 각 페이지 안의 학과 링크와 공지 링크는 crawler가 확장 탐색한다.
    department_sources: List[str] = [
        "https://www.cbnu.ac.kr/www/contents.do?key=391",   # 인문대학
        "https://www.cbnu.ac.kr/www/contents.do?key=392",   # 사회과학대학
        "https://www.cbnu.ac.kr/www/contents.do?key=393",   # 자연과학대학
        "https://www.cbnu.ac.kr/www/contents.do?key=394",   # 경영대학
        "https://www.cbnu.ac.kr/www/contents.do?key=395",   # 공과대학
        "https://www.cbnu.ac.kr/www/contents.do?key=396",   # 전자정보대학
        "https://www.cbnu.ac.kr/www/contents.do?key=397",   # 농업생명환경대학
        "https://www.cbnu.ac.kr/www/contents.do?key=398",   # 사범대학
        "https://www.cbnu.ac.kr/www/contents.do?key=399",   # 생활과학대학
        "https://www.cbnu.ac.kr/www/contents.do?key=400",   # 수의과대학
        "https://www.cbnu.ac.kr/www/contents.do?key=401",   # 약학대학
        "https://www.cbnu.ac.kr/www/contents.do?key=402",   # 의과대학
        "https://www.cbnu.ac.kr/www/contents.do?key=1294",  # 간호대학
        "https://www.cbnu.ac.kr/www/contents.do?key=404",   # 창의융합대학
        "https://www.cbnu.ac.kr/www/contents.do?key=403",   # 충북PRIDE공유대학
        "https://www.cbnu.ac.kr/www/contents.do?key=405",   # 예술학과군
    ]

    extra_sources: List[str] = Field(default_factory=lambda: parse_source_urls(os.getenv("CBNU_EXTRA_SOURCES")))

    @property
    def default_sources(self) -> List[str]:
        return list(dict.fromkeys([*self.core_sources, *self.department_sources, *self.extra_sources]))

    @property
    def academic_schedule_start(self) -> date:
        return date(self.current_year, 1, 1)

    @property
    def academic_schedule_end(self) -> date:
        return date(self.current_year, 12, 31)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    root = settings.project_root
    settings.data_dir = settings.data_dir if settings.data_dir.is_absolute() else root / settings.data_dir
    settings.upload_dir = settings.upload_dir if settings.upload_dir.is_absolute() else root / settings.upload_dir
    settings.chroma_dir = settings.chroma_dir if settings.chroma_dir.is_absolute() else root / settings.chroma_dir
    settings.sqlite_path = settings.sqlite_path if settings.sqlite_path.is_absolute() else root / settings.sqlite_path
    for path in (settings.data_dir, settings.upload_dir, settings.chroma_dir, settings.sqlite_path.parent):
        path.mkdir(parents=True, exist_ok=True)
    return settings
