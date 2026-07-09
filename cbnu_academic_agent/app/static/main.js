const form = document.getElementById("chat-form");
const input = document.getElementById("message");
const messages = document.getElementById("messages");
const profileForm = document.getElementById("profile-form");
const profileStatus = document.getElementById("profile-status");
const profileFields = {
  name: document.getElementById("profile-name"),
  college: document.getElementById("profile-college"),
  department: document.getElementById("profile-department"),
  grade: document.getElementById("profile-grade"),
  student_type: document.getElementById("profile-student-type"),
  interests: document.getElementById("profile-interests"),
  memo: document.getElementById("profile-memo"),
};
const syncBtn = document.getElementById("sync-btn");
const syncStatus = document.getElementById("sync-status");
const calendarSummary = document.getElementById("calendar-summary");
const calendarMonth = document.getElementById("calendar-month");
const calendarGrid = document.getElementById("calendar-grid");
const calendarList = document.getElementById("calendar-list");
const changeList = document.getElementById("change-list");
const prevMonth = document.getElementById("prev-month");
const nextMonth = document.getElementById("next-month");
const toggleCalendarRange = document.getElementById("toggle-calendar-range");
const refreshCalendar = document.getElementById("refresh-calendar");
const refreshChanges = document.getElementById("refresh-changes");
const todoList = document.getElementById("todo-list");
const sessionId = localStorage.getItem("cbnu_session_id") || crypto.randomUUID();
localStorage.setItem("cbnu_session_id", sessionId);
let calendarEvents = [];
let visibleMonth = new Date();
visibleMonth.setDate(1);
let showFullCalendarRange = false;
const displayYear = new Date().getFullYear();

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

function addSources(sources) {
  if (!sources || sources.length === 0) return;
  const div = document.createElement("div");
  div.className = "sources";
  div.innerHTML = "<b>출처</b><br>" + sources.map((s, idx) => {
    const safeTitle = s.title || `출처 ${idx + 1}`;
    return `${idx + 1}. <a href="${s.url}" target="_blank" rel="noreferrer">${safeTitle}</a>`;
  }).join("<br>");
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addMessage("user", text);
  addMessage("system", "실시간 크롤링과 RAG 검색을 수행 중입니다...");

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });
    const data = await res.json();
    const systemMsgs = document.querySelectorAll(".msg.system");
    systemMsgs[systemMsgs.length - 1]?.remove();

    if (!res.ok) {
      addMessage("assistant", data.detail || "오류가 발생했습니다.");
      return;
    }
    addMessage("assistant", data.answer);
    addSources(data.sources);
    if (data.todos && data.todos.length > 0) {
      renderTodoResult(data.todos, data.calendar_events || []);
    }
    if ((data.schedules && data.schedules.length > 0) || (data.calendar_events && data.calendar_events.length > 0)) {
      loadCalendar();
    }
  } catch (err) {
    addMessage("assistant", "서버와 통신하지 못했습니다. FastAPI 서버 실행 상태를 확인하세요.");
  }
});

profileForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  profileStatus.textContent = "저장 중...";
  const payload = collectProfilePayload();
  const res = await fetch("/api/profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  profileStatus.textContent = res.ok
    ? data.message || "프로필 저장 완료"
    : data.detail || "프로필 저장 실패";
});

function collectProfilePayload() {
  return {
    session_id: sessionId,
    name: emptyToNull(profileFields.name.value),
    college: emptyToNull(profileFields.college.value),
    department: emptyToNull(profileFields.department.value),
    grade: emptyToNull(profileFields.grade.value),
    student_type: emptyToNull(profileFields.student_type.value),
    interests: profileFields.interests.value.split(",").map((item) => item.trim()).filter(Boolean),
    memo: profileFields.memo.value.trim(),
  };
}

function emptyToNull(value) {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

async function loadProfile() {
  const res = await fetch(`/api/profile/${encodeURIComponent(sessionId)}`);
  if (!res.ok) return;
  const data = await res.json();
  const profile = data.profile || {};
  profileFields.name.value = profile.name || "";
  profileFields.college.value = profile.college || "";
  profileFields.department.value = profile.department || "";
  profileFields.grade.value = profile.grade || "";
  profileFields.student_type.value = profile.student_type || "";
  profileFields.interests.value = (profile.interests || []).join(", ");
  profileFields.memo.value = profile.memo || "";
}

syncBtn.addEventListener("click", async () => {
  syncStatus.textContent = "동기화 중...";
  const res = await fetch("/api/crawl/sync", { method: "POST" });
  const data = await res.json();
  if (!res.ok) {
    syncStatus.textContent = data.detail || "동기화 실패";
    return;
  }
  syncStatus.textContent = `신규 ${data.new_count}, 변경 ${data.changed_count}, 동일 ${data.unchanged_count}, 색인 ${data.indexed_documents}`;
  loadCalendar();
  loadChanges();
});

function renderTodoResult(todos, calendarEvents) {
  const scheduledTodos = todos.filter((todo) => todo.due_date);
  if (scheduledTodos.length === 0) {
    todoList.innerHTML = `<li class="todo-calendar-result"><b>Calendar 작성 안 됨</b><span>현재 날짜 이후의 관련 일정이 없어 Todo 날짜를 지정하지 않았습니다.</span></li>`;
    return;
  }
  todoList.innerHTML = scheduledTodos.map((todo) => (
    `<li><b>${todo.title}</b><span>${todo.due_date} · ${todo.priority}</span><small>${todo.reason || ""}</small></li>`
  )).join("") + `<li class="todo-calendar-result"><b>Calendar 작성 완료</b><span>${calendarEvents.length}개 Todo가 달력에 반영되었습니다.</span></li>`;
}

refreshCalendar.addEventListener("click", loadCalendar);
prevMonth.addEventListener("click", () => {
  visibleMonth.setMonth(visibleMonth.getMonth() - 1);
  showFullCalendarRange = false;
  renderCalendar();
});
nextMonth.addEventListener("click", () => {
  visibleMonth.setMonth(visibleMonth.getMonth() + 1);
  showFullCalendarRange = false;
  renderCalendar();
});
toggleCalendarRange.addEventListener("click", () => {
  showFullCalendarRange = !showFullCalendarRange;
  renderCalendar();
});
refreshChanges.addEventListener("click", loadChanges);

async function loadCalendar() {
  const res = await fetch("/api/calendar");
  const events = await res.json();
  if (!res.ok) {
    calendarSummary.innerHTML = "";
    calendarGrid.innerHTML = "";
    calendarList.innerHTML = "<p class=\"empty-text\">캘린더를 불러오지 못했습니다.</p>";
    return;
  }

  calendarEvents = [...events].sort((a, b) => {
    const aDate = eventDate(a) || "9999-12-31";
    const bDate = eventDate(b) || "9999-12-31";
    return aDate.localeCompare(bDate);
  });
  visibleMonth = new Date(displayYear, new Date().getMonth(), 1);
  renderCalendar();
}

function eventDate(event) {
  return event.deadline || event.start_date || event.end_date || "";
}

function renderCalendarSummary(events) {
  const datedEvents = events.filter((event) => eventDate(event));
  const today = new Date().toISOString().slice(0, 10);
  const upcoming = datedEvents.find((event) => eventDate(event) >= today);
  const highCount = events.filter((event) => event.importance === "high").length;
  const firstDate = `${displayYear}-01-01`;
  const lastDate = `${displayYear}-12-31`;

  calendarSummary.innerHTML = `
    <div class="summary-item"><b>${events.length}</b><span>전체 일정</span></div>
    <div class="summary-item"><b>${highCount}</b><span>중요 일정</span></div>
    <div class="summary-item"><b>${firstDate} ~ ${lastDate}</b><span>표시 범위</span></div>
    <div class="summary-item"><b>${upcoming ? eventDate(upcoming) : "없음"}</b><span>다가오는 일정</span></div>
  `;
}

function renderCalendar() {
  renderCalendarSummary(calendarEvents);
  toggleCalendarRange.textContent = showFullCalendarRange ? "월간 보기" : "전체 보기";

  if (showFullCalendarRange) {
    calendarGrid.className = "calendar-range";
    renderFullRangeCalendar();
    return;
  }

  calendarGrid.className = "calendar-grid";
  calendarMonth.textContent = `${visibleMonth.getFullYear()}년 ${visibleMonth.getMonth() + 1}월`;

  const monthEvents = calendarEvents.filter((event) => eventDate(event).startsWith(monthKey(visibleMonth)));
  const eventsByDate = groupEventsByDate(monthEvents);
  calendarGrid.innerHTML = calendarGridHtml(visibleMonth, eventsByDate);
  calendarList.innerHTML = monthEvents.length
    ? `<h3 class="month-heading">이 달의 일정</h3>${monthAgendaHtml(monthEvents)}`
    : "<p class=\"empty-text\">이 달에 표시할 일정이 없습니다. 동기화 후 일정이 있으면 날짜 칸에 표시됩니다.</p>";
}

function renderFullRangeCalendar() {
  const months = monthsInDisplayYear();

  calendarMonth.textContent = `${monthLabel(months[0])} ~ ${monthLabel(months[months.length - 1])}`;
  calendarGrid.innerHTML = months.map((monthDate) => {
    const monthEvents = calendarEvents.filter((event) => eventDate(event).startsWith(monthKey(monthDate)));
    const eventsByDate = groupEventsByDate(monthEvents);
    return `<section class="calendar-month-block">
      <h3>${monthLabel(monthDate)}</h3>
      <div class="calendar-grid mini-calendar">${calendarGridHtml(monthDate, eventsByDate)}</div>
    </section>`;
  }).join("");
  calendarList.innerHTML = `<h3 class="month-heading">전체 일정</h3>${monthAgendaHtml(calendarEvents)}`;
}

function monthsInDisplayYear() {
  return Array.from({ length: 12 }, (_, idx) => new Date(displayYear, idx, 1));
}

function monthLabel(date) {
  return `${date.getFullYear()}년 ${date.getMonth() + 1}월`;
}

function monthKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function groupEventsByDate(events) {
  return events.reduce((acc, event) => {
    const key = eventDate(event);
    if (!key) return acc;
    acc[key] = acc[key] || [];
    acc[key].push(event);
    return acc;
  }, {});
}

function calendarGridHtml(monthDate, eventsByDate) {
  const weekdays = ["일", "월", "화", "수", "목", "금", "토"];
  const year = monthDate.getFullYear();
  const month = monthDate.getMonth();
  const firstDay = new Date(year, month, 1);
  const firstCell = new Date(year, month, 1 - firstDay.getDay());
  const todayText = new Date().toISOString().slice(0, 10);
  const cells = [];

  for (let i = 0; i < 42; i += 1) {
    const cellDate = new Date(firstCell);
    cellDate.setDate(firstCell.getDate() + i);
    const dateText = toDateText(cellDate);
    const dayEvents = eventsByDate[dateText] || [];
    const outside = cellDate.getMonth() !== month ? " outside-month" : "";
    const today = dateText === todayText ? " today" : "";
    const badges = dayEvents.slice(0, 3).map((event) => eventBadgeHtml(event)).join("");
    const more = dayEvents.length > 3 ? `<span class="event-more">+${dayEvents.length - 3}</span>` : "";
    cells.push(`<button class="calendar-day${outside}${today}" type="button" title="${dateText}">
      <span class="day-number">${cellDate.getDate()}</span>
      <div class="day-events">${badges}${more}</div>
    </button>`);
  }

  return `
    ${weekdays.map((day) => `<div class="weekday">${day}</div>`).join("")}
    ${cells.join("")}
  `;
}

function toDateText(date) {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}

function eventBadgeHtml(event) {
  const priority = event.importance === "high" ? " high" : "";
  const todo = event.change_type === "todo" ? " todo" : "";
  return `<span class="event-badge${priority}${todo}">${event.title}</span>`;
}

function monthAgendaHtml(events) {
  return events.map((event) => {
    const date = eventDate(event) || "날짜 미정";
    const source = event.source_url
      ? `<a href="${event.source_url}" target="_blank" rel="noreferrer">출처</a>`
      : "";
    return `<article class="calendar-item">
      <time>${date}</time>
      <div>
        <b>${event.title}</b>
        <span>${event.category} · ${event.importance} · ${event.change_type}</span>
        <p>${event.evidence || ""}</p>
        ${source}
      </div>
    </article>`;
  }).join("");
}

async function loadChanges() {
  const res = await fetch("/api/changes");
  const changes = await res.json();
  if (!res.ok || changes.length === 0) {
    changeList.innerHTML = "<p class=\"empty-text\">변경 감지 내역이 없습니다.</p>";
    return;
  }
  changeList.innerHTML = changes.map((item) => (
    `<article class="change-item">
      <b>${item.change_type.toUpperCase()}</b>
      <a href="${item.source_url}" target="_blank" rel="noreferrer">${item.title}</a>
      <span>${item.detected_at}</span>
    </article>`
  )).join("");
}

loadCalendar();
loadChanges();
loadProfile();
