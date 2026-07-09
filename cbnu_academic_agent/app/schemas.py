from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(default="default", min_length=1, max_length=100)


class SourceItem(BaseModel):
    title: str
    url: str
    snippet: str = ""
    score: Optional[float] = None


class AcademicSchedule(BaseModel):
    title: str = Field(..., description="일정 제목")
    category: Literal["학사", "수강", "장학", "졸업", "등록", "시험", "휴복학", "기타"]
    start_date: Optional[str] = Field(default=None, description="YYYY-MM-DD 형식, 알 수 없으면 null")
    end_date: Optional[str] = Field(default=None, description="YYYY-MM-DD 형식, 알 수 없으면 null")
    deadline: Optional[str] = Field(default=None, description="마감일 YYYY-MM-DD, 알 수 없으면 null")
    importance: Literal["high", "medium", "low"] = "medium"
    source_url: Optional[str] = None
    evidence: str = Field(default="", description="근거가 된 짧은 원문 또는 요약")


class AcademicScheduleList(BaseModel):
    schedules: list[AcademicSchedule] = Field(default_factory=list)


class UploadResponse(BaseModel):
    filename: str
    chunks: int
    collection: str
    message: str


class CrawlSyncResponse(BaseModel):
    indexed_documents: int
    new_count: int
    changed_count: int
    unchanged_count: int
    calendar_events: list[AcademicSchedule] = Field(default_factory=list)


class CalendarEvent(BaseModel):
    id: int
    title: str
    category: str = "기타"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    deadline: Optional[str] = None
    importance: str = "medium"
    source_url: Optional[str] = None
    evidence: str = ""
    change_type: str = "manual"


class ChangeItem(BaseModel):
    id: int
    title: str
    source_url: str
    change_type: str
    content_hash: str
    detected_at: str


class ProfileSettings(BaseModel):
    session_id: str = Field(default="default", min_length=1, max_length=100)
    name: Optional[str] = Field(default=None, max_length=100)
    college: Optional[str] = Field(default=None, max_length=100)
    department: Optional[str] = Field(default=None, max_length=100)
    grade: Optional[str] = Field(default=None, max_length=50)
    student_type: Optional[str] = Field(default=None, max_length=100)
    interests: list[str] = Field(default_factory=list)
    memo: str = Field(default="", max_length=1000)


class ProfileResponse(BaseModel):
    profile: ProfileSettings
    message: str = ""


class TodoBreakdownRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=1000)


class TodoItem(BaseModel):
    title: str
    due_date: Optional[str] = None
    priority: Literal["high", "medium", "low"] = "medium"
    reason: str = ""


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    route: str
    sources: list[SourceItem] = Field(default_factory=list)
    schedules: list[AcademicSchedule] = Field(default_factory=list)
    todos: list[TodoItem] = Field(default_factory=list)
    calendar_events: list[CalendarEvent] = Field(default_factory=list)


class TodoBreakdownResponse(BaseModel):
    todos: list[TodoItem] = Field(default_factory=list)
    calendar_events: list[CalendarEvent] = Field(default_factory=list)
