# CBNU Academic Planner Agent

충북대학교 학사 일정, 공지, 개인 프로필을 바탕으로 학생에게 필요한 일정과 할 일을 정리해주는 FastAPI + LangGraph 기반 학사 플래너 Agent입니다.

사용자는 웹 채팅창에 자연어로 질문하고, Agent는 요청을 분류한 뒤 필요한 Tool과 RAG 파이프라인을 실행합니다. Calendar UI에는 학사 일정, 공지 변경 감지 결과, 채팅에서 추출된 일정, Todo 일정이 함께 표시됩니다.

## 문제 정의

충북대학교 학생들은 학사 일정, 공지사항, 제출 기한, 수강 관련 정보 등을 확인하기 위해 여러 웹페이지를 직접 방문해야 한다. 또한 일부 정보만 알림으로 제공되기 때문에, 학생이 모든 중요 일정을 지속적으로 확인하지 않으면 필요한 정보를 놓칠 가능성이 있다. 특히 장학금 신청, 학부 연계 대회, 수강 신청, 서류 제출과 같은 일정은 마감일을 놓칠 경우 학업적 기회 상실이나 행정적 불이익으로 이어질 수 있다.

따라서 본 프로그램은 분산된 학사 정보를 자동으로 수집하고, 공지 변경 사항을 감지하며, 사용자 프로필에 따라 필요한 일정과 할 일을 정리해주는 개인화 학사 일정 관리 Agent를 구현하고자 한다.

## 주요 기능

- FastAPI 기반 Web Chat UI
- LangGraph `StateGraph` 기반 Agent 실행 흐름
- 자연어 요청 분류: 학사 RAG, 날짜 계산, Todo 분해, 가드레일
- 충북대학교 공식/단과대학/추가 URL 실시간 크롤링
- Persistent Chroma + Runtime 문서 기반 Hybrid RAG
- 현재 연도 1월 1일부터 12월 31일까지 Calendar 표시
- 공지 신규/변경 감지 결과 SQLite 저장
- 학사 일정/공지/채팅 추출 일정 Calendar 저장
- 채팅 기반 Todo 자동 분해 및 Calendar 반영
- 요청 시각 기준 이후 날짜의 Todo만 생성/표시
- 직접 입력형 사용자 Profile 설정
- Profile을 Agent 검색/답변/Todo 생성에 반영
- LangGraph `InMemorySaver` 기반 멀티턴 대화
- Request Logging Middleware 적용

## 사용 예시

```text
이번 달 충북대 학사일정 중 중요한 것만 정리해줘
수강신청 관련 공지 찾아줘
장학금 신청 마감일이 있는지 알려줘
2026-08-05까지 며칠 남았어?
장학금 신청 준비를 할 일로 나눠줘
내 학과 기준으로 중요한 공지만 알려줘
```

## 실행 흐름

```mermaid
flowchart TD
    client[Web UI] --> middleware[RequestLoggingMiddleware]
    middleware --> chat[POST /api/chat]
    profile[Profile 설정 JSON] --> chat
    chat --> graph[LangGraph StateGraph]

    graph --> classify[classify_request]
    classify -->|academic_rag| crawl[realtime_cbnu_crawl_tool]
    classify -->|date_calc| datecalc[date_calculator_tool]
    classify -->|todo| todo[todo_breakdown_tool]
    classify -->|guardrail| guardrail[guardrail 응답]

    crawl --> rag[runtime_rag_search_tool]
    rag -->|검색 부족| expand[expand_query]
    expand --> crawl
    rag -->|검색 충분| extract[OutputParser 일정 추출]
    extract --> answer[최종 답변]

    todo --> todo_filter[요청 시각 이후 Todo 필터링]
    todo_filter --> todo_calendar[Todo Calendar 저장]
    answer --> schedule_calendar[채팅 추출 일정 Calendar 저장]

    sync[POST /api/crawl/sync] --> schedule_loader[학사 일정 로더]
    sync --> notice_crawl[공지 크롤링]
    notice_crawl --> chroma[Chroma 저장]
    notice_crawl --> change_detect[SQLite 변경 감지]
    change_detect --> calendar[Calendar 이벤트]
    schedule_loader --> calendar

    calendar --> ui_calendar[Calendar UI]
```

서버 실행 후 LangGraph 다이어그램은 다음 주소에서도 확인할 수 있습니다.

```text
http://127.0.0.1:8000/api/graph/mermaid
```

## 설치

### 1. 가상환경 생성

```bash
python -m venv .venv
```

Windows PowerShell:

```bash
.venv\Scripts\activate
```

macOS/Linux:

```bash
source .venv/bin/activate
```

### 2. 패키지 설치

```bash
pip install -r requirements.txt
```

주요 패키지는 다음 계열을 사용합니다.

- `langchain>=1.0.0`
- `langgraph>=1.0.0`
- `langchain-openai>=1.0.0`
- `langchain-chroma>=0.2.0`
- `fastapi>=0.115.0`
- `chromadb>=0.5.0`

### 3. 환경변수 설정

`.env.example`을 복사해 `.env`를 만듭니다.

```bash
copy .env.example .env
```

macOS/Linux:

```bash
cp .env.example .env
```

`.env` 예시:

```env
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
CBNU_EXTRA_SOURCES=https://department1.example.edu,https://department2.example.edu
```

`CBNU_EXTRA_SOURCES`는 선택값입니다. 기본 충북대학교 공식/단과대학 URL 외에 특정 학과 홈페이지를 더 크롤링하고 싶을 때 쉼표로 구분해 추가합니다.

### 4. 서버 실행

```bash
uvicorn app.main:app --reload
```

브라우저 접속:

```text
http://127.0.0.1:8000
```

## Web UI

### Profile

직접 입력 방식으로 개인화를 설정합니다.

입력 항목:

- 이름
- 단과대학
- 학과
- 학년
- 학적/구분
- 관심 항목
- 추가 메모

저장된 Profile은 브라우저 `session_id` 기준으로 `data/profiles.json`에 저장됩니다. 이후 채팅 요청 시 Agent의 `request_metadata.profile`에 포함되어 검색어 보강, 답변 우선순위, Todo 생성에 반영됩니다.

### Chat

사용자 자연어 요청을 받아 LangGraph Agent를 실행합니다. Agent는 요청을 분류하고 필요한 Tool을 실행합니다.

### Todo

별도 Todo 입력칸은 없습니다. 채팅창에서 Todo 생성을 요청하면 Agent가 `todo_breakdown_tool`을 실행합니다.

Todo는 요청 시각의 날짜를 기준으로 이후 날짜만 표시/저장됩니다. 관련 학사 일정이 Calendar DB에 있어야 날짜 지정 품질이 좋아집니다.

### Calendar

서버 실행 시점의 현재 연도를 기준으로 `YYYY-01-01`부터 `YYYY-12-31`까지의 일정을 표시합니다.

Calendar에는 다음 항목이 표시됩니다.

- 학사 일정 동기화 결과
- 공지 변경 감지에서 추출된 일정
- 채팅에서 추출된 일정
- Todo 자동 분해 결과

## API

### Health

```http
GET /api/health
```

### Chat

```http
POST /api/chat
```

요청:

```json
{
  "message": "장학금 신청 준비를 할 일로 나눠줘",
  "session_id": "browser-session-id"
}
```

응답에는 `answer`, `route`, `sources`, `schedules`, `todos`, `calendar_events`가 포함됩니다.

### Profile 저장

```http
POST /api/profile
```

요청:

```json
{
  "session_id": "browser-session-id",
  "name": "홍길동",
  "college": "전자정보대학",
  "department": "소프트웨어학부",
  "grade": "3학년",
  "student_type": "재학생",
  "interests": ["수강", "장학", "졸업"],
  "memo": "졸업 요건과 장학 공지를 우선 확인"
}
```

### Profile 조회

```http
GET /api/profile/{session_id}
```

### Profile PDF 업로드

```http
POST /api/profile/upload
```

레거시/보조 기능입니다. PDF 텍스트를 추출해 `user_profile_pdf` Chroma collection에 저장합니다. 현재 Web UI의 기본 개인화 흐름은 직접 입력 Profile입니다.

### 공지/학사일정 동기화

```http
POST /api/crawl/sync
```

동작:

1. 충북대학교 학사 일정 페이지에서 현재 연도 전체 일정을 수집
2. 기본/단과대학/추가 URL을 크롤링
3. 문서를 Chroma에 저장
4. URL별 content hash를 SQLite에 저장
5. 신규/변경 공지를 감지
6. 일정 후보를 Calendar DB에 저장

### Calendar

```http
GET /api/calendar
```

현재 연도 범위의 Calendar 이벤트를 반환합니다.

### 변경 감지 이력

```http
GET /api/changes
```

SQLite에 기록된 신규/변경 공지 이력을 반환합니다.

### Todo 자동 분해

```http
POST /api/todos/breakdown
```

보조 API입니다. Web UI에서는 별도 입력칸 없이 `/api/chat`을 통해 Todo를 생성합니다.

### Mermaid Graph

```http
GET /api/graph/mermaid
```

LangGraph 실행 흐름을 Mermaid 텍스트로 반환합니다.

## LangGraph Agent

Agent 상태는 `app/agent/graph.py`의 `AgentState`로 관리합니다.

주요 상태:

- `messages`
- `query`
- `rewritten_query`
- `route`
- `raw_docs`
- `context_docs`
- `schedules`
- `todos`
- `answer`
- `request_metadata`

노드:

- `classify_request`
- `crawl_realtime_web`
- `rag_search`
- `expand_query`
- `extract_schedule`
- `answer`
- `date_calc`
- `todo`
- `guardrail`

조건부 분기:

- `classify_request` 결과에 따라 `academic_rag`, `date_calc`, `todo`, `guardrail`로 이동
- `rag_search` 결과가 부족하면 `expand_query` 후 재검색, 충분하면 일정 추출로 이동

Memory:

- `InMemorySaver` checkpointer 사용
- Web UI는 `localStorage`에 `cbnu_session_id`를 저장
- 같은 `thread_id`로 멀티턴 대화 유지

## Tools

### `realtime_cbnu_crawl_tool`

충북대학교 기본/단과대학/추가 URL을 실시간 크롤링해 관련 문서 후보를 가져옵니다.

### `runtime_rag_search_tool`

실시간 크롤링 문서를 Chroma에 저장하고, Persistent Chroma와 Runtime 문서를 함께 검색합니다.

### `date_calculator_tool`

`YYYY-MM-DD` 또는 `YYYY.MM.DD` 형식 날짜까지 남은 기간을 계산합니다.

### `todo_breakdown_tool`

학사 목표를 실행 가능한 Todo 목록으로 분해합니다. `reference_date`를 받아 요청일 이후의 일정으로 조정합니다.

## RAG 구성

RAG 파이프라인은 `app/services/rag.py`, `app/services/vector_db.py`, `app/agent/tools.py`에 걸쳐 구성됩니다.

1. 크롤러가 충북대학교 관련 문서를 수집
2. `Document`로 변환
3. `cbnu_academic_docs` Chroma collection에 저장
4. 요청 시점의 Runtime 문서와 Persistent Chroma 문서를 함께 검색
5. 검색 결과를 `answer_node`의 LLM 프롬프트에 제공

Chroma 저장 경로:

```text
data/chroma/
├── cbnu_academic_docs/
└── user_profile_pdf/
```

`user_profile_pdf`는 PDF 업로드 보조 기능에서 사용됩니다.

## SQLite 저장소

SQLite 경로:

```text
data/cbnu_agent.db
```

주요 테이블:

- `notices`: URL별 최신 content hash
- `notice_changes`: 신규/변경 감지 이력
- `calendar_events`: Calendar UI에 표시할 일정

## Middleware

`RequestLoggingMiddleware`가 적용되어 있습니다.

기능:

- 요청 ID 생성
- 요청 시각 `requested_at` 기록
- HTTP method/path 로깅
- 상태 코드와 처리 시간 로깅
- `/api/chat` 요청에 Agent용 메타데이터 부착

Todo 생성은 이 `requested_at`을 기준으로 현재 이후 일정만 유지합니다.

## OutputParser

`PydanticOutputParser`를 사용해 검색 문맥에서 학사 일정 JSON을 추출합니다.

```python
class AcademicSchedule(BaseModel):
    title: str
    category: Literal["학사", "수강", "장학", "졸업", "등록", "시험", "휴복학", "기타"]
    start_date: Optional[str]
    end_date: Optional[str]
    deadline: Optional[str]
    importance: Literal["high", "medium", "low"]
    source_url: Optional[str]
    evidence: str
```

## 프로젝트 구조

```text
cbnu_academic_agent/
├── app/
│   ├── agent/
│   │   ├── graph.py
│   │   └── tools.py
│   ├── middleware/
│   │   └── logging.py
│   ├── services/
│   │   ├── academic_schedule.py
│   │   ├── change_store.py
│   │   ├── crawler.py
│   │   ├── date_utils.py
│   │   ├── profile_store.py
│   │   ├── rag.py
│   │   ├── todo.py
│   │   └── vector_db.py
│   ├── static/
│   │   ├── index.html
│   │   ├── main.js
│   │   └── style.css
│   ├── config.py
│   ├── main.py
│   └── schemas.py
├── data/
│   ├── cbnu_agent.db
│   ├── chroma/
│   └── profiles.json
├── scripts/
│   └── export_graph.py
├── requirements.txt
├── .env.example
├── workflow.mmd
└── README.md
```

## 구현 체크리스트

- FastAPI + LangGraph Agent 유지
- 사용자 Profile 직접 설정 기능 추가
- Profile을 Agent 요청 메타데이터에 반영
- PDF 업로드 후 Chroma 저장 보조 기능 유지
- 학사 일정/공지 크롤링 후 Chroma 저장
- Calendar UI 추가
- 공지 변경 감지용 SQLite 추가
- 변경 감지 결과 Calendar 반영
- Todo 자동 분해 Tool 추가
- Todo를 채팅 기반으로 생성
- 요청 시각 이후 Todo만 Calendar에 표시
- README 및 Workflow 문서 최신화

## 한계 및 개선 방향

- Chroma/SQLite/프로필 JSON은 로컬 파일 기반입니다. 운영 환경에서는 사용자 인증, 백업, 동시성 전략이 필요합니다.
- `InMemorySaver` 기반 대화 이력은 프로세스 재시작 시 사라집니다.
- 학교 홈페이지 HTML 구조가 바뀌면 크롤링 품질이 달라질 수 있습니다.
- HWP 등 첨부파일 파싱은 현재 범위에 포함하지 않았습니다.
- 향후 Google Calendar 연동, 알림, 사용자별 로그인, Profile DB 저장, 일정 수정/삭제 UI를 추가할 수 있습니다.

## 참고

- LangChain / LangGraph
- FastAPI
- Chroma
- 충북대학교 공식 홈페이지 및 학사일정/공지 페이지
