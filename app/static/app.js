const state = {
  config: null,
  diagnostics: null,
  diagnosticsFilterEmail: null,
  expandedGroupBundles: new Set(),
  expandedAllDayDays: new Set(),
  isMobileSidebarOpen: false,
  lastRenderedWeeks: null,
  overlapSplit: false,
  selectedDate: startOfDay(new Date()),
  setup: null,
  personOrderEmails: null,
  visiblePersonEmails: null,
  visibleProjectCodes: null,
  lastSearchKeyword: "",
  selectedGroupId: null,
  selectedPeople: new Set(),
  selectedProjects: new Set(),
  selectedTypes: new Set(["important", "project", "work", "attendance", "other"]),
  viewMode: "week",
  weekStart: startOfView(new Date()),
};

const typeLabels = {
  important: "공식행사",
  project: "[과제]",
  work: "[업무]",
  attendance: "[근태]",
  other: "[기타]",
};

const groupedGroupIds = new Set([
  "contract-research-assistants",
  "aging-society-center",
  "public-investment-center",
  "balanced-development-center",
  "social-welfare-center",
  "groundwater-center",
  "carbon-neutrality-center",
]);

const groupedGroupBundle = {
  id: "centers-and-assistants",
  name: "기타 센터·위촉연구원",
};

document.addEventListener("DOMContentLoaded", async () => {
  syncPlatformChrome();
  bindToolbar();
  bindCalendarGestures();
  bindGlobalCollapse();
  await loadConfig();
  await loadDiagnostics();
});

function bindToolbar() {
  document.getElementById("menu-button").addEventListener("click", toggleMobileSidebar);
  document.getElementById("sidebar-backdrop").addEventListener("click", closeMobileSidebar);
  document.getElementById("clear-selection-sidebar").addEventListener("click", async () => {
    clearSelection();
    await loadEvents();
  });
  document.getElementById("selection-search").addEventListener("input", () => {
    renderSelectionLists(true);
  });
  document.getElementById("overlap-split-toggle").addEventListener("change", (event) => {
    state.overlapSplit = event.target.checked;
    renderCalendar(state.lastRenderedWeeks || [], { scrollTop: document.querySelector(".calendar-shell")?.scrollTop || 0 });
  });
  document.getElementById("view-mode").addEventListener("change", async (event) => {
    state.viewMode = event.target.value;
    if (state.viewMode === "day") {
      state.selectedDate = startOfDay(new Date());
      state.weekStart = startOfView(state.selectedDate);
    }
    await loadEvents();
  });
  document.getElementById("prev-week").addEventListener("click", () => shiftView(-1));
  document.getElementById("next-week").addEventListener("click", () => shiftView(1));
  document.getElementById("today-button").addEventListener("click", async () => {
    const today = new Date();
    state.selectedDate = startOfDay(today);
    state.weekStart = startOfView(today);
    await loadEvents();
  });
  window.addEventListener("resize", syncMobileChrome);
  window.addEventListener("orientationchange", syncMobileChrome);
  window.addEventListener("pageshow", syncMobileChrome);
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", syncMobileChrome);
  }
  syncMobileChrome();
  window.setTimeout(syncMobileChrome, 120);
  window.setTimeout(syncMobileChrome, 420);
}

async function loadConfig() {
  const response = await fetch("/api/config");
  const payload = await response.json();
  state.config = payload;
  state.personOrderEmails = initialPersonOrder(payload.people || []);
  state.visiblePersonEmails = null;
  state.visibleProjectCodes = null;
  document.title = payload.title || "일정조회";
  document.getElementById("app-title").textContent = payload.title;
  renderSetupState(payload.setup);
  renderGroups();
  renderSelectionLists(true);
  renderTypeFilters();
  updateSelectionCopy();
  await loadEvents();
}

async function loadDiagnostics() {
  try {
    const response = await fetch("/api/diagnostics");
    const payload = await response.json();
    if (!response.ok) return;
    state.diagnostics = payload;
    renderDiagnostics(payload);
    renderSelectionLists(false);
  } catch (_error) {
    // Keep the main calendar usable even if diagnostics are slow or fail.
  }
}

function renderSetupState(setup) {
  state.setup = setup;
  const node = document.getElementById("setup-state");
  const statusNode = document.getElementById("sync-status");
  const alertNode = document.getElementById("service-alert");
  if (alertNode) {
    alertNode.hidden = true;
    alertNode.textContent = "";
  }
  if (setup.demo_mode) {
    node.textContent = "데모 모드로 실행 중";
    statusNode.textContent = "";
    return;
  }
  if (setup.google_ready) {
    node.textContent = `Google 연동 준비됨 / subject: ${setup.directory_subject}`;
    const synced = `${setup.sync_percent}%`;
    const lastSynced = setup.last_synced_at
      ? `마지막 동기화 ${formatDateTime(new Date(setup.last_synced_at))}`
      : "아직 초기 동기화 중";
    statusNode.textContent = `캐시 준비 ${synced} / ${lastSynced}`;
    const initialSyncComplete = setup.initial_sync_complete ?? setup.sync_percent >= 100;
    if (alertNode && !initialSyncComplete) {
      alertNode.hidden = false;
      alertNode.textContent = "일정 동기화중: 일부 캘린더가 보이지 않을 수 있습니다";
    }
    return;
  }
  node.textContent = "Google 연동 미설정. 데모 모드 또는 환경 변수 확인 필요";
  statusNode.textContent = "";
}

function renderGroups() {
  const list = document.getElementById("group-list");
  list.innerHTML = "";
  const bundledGroups = state.config.groups.filter((group) => groupedGroupIds.has(group.id));
  const selectedBundledGroup = bundledGroups.some((group) => group.id === state.selectedGroupId);
  const bundleExpanded = state.expandedGroupBundles.has(groupedGroupBundle.id) || selectedBundledGroup;
  let bundleRendered = false;

  state.config.groups.forEach((group) => {
    if (groupedGroupIds.has(group.id)) {
      if (!bundleRendered) {
        renderGroupBundle(list, bundledGroups, bundleExpanded, selectedBundledGroup);
        bundleRendered = true;
      }
      if (!bundleExpanded) return;
    }
    renderGroupButton(list, group, groupedGroupIds.has(group.id));
  });
}

function renderGroupBundle(list, groups, expanded, selected) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `group-bundle-button${expanded ? " expanded" : ""}${selected ? " active" : ""}`;
  button.innerHTML = `<span class="bundle-caret">${expanded ? "▾" : "▸"}</span>${groupedGroupBundle.name}<span class="count">${groups.length}개</span>`;
  button.addEventListener("click", () => {
    if (state.expandedGroupBundles.has(groupedGroupBundle.id)) {
      state.expandedGroupBundles.delete(groupedGroupBundle.id);
    } else {
      state.expandedGroupBundles.add(groupedGroupBundle.id);
    }
    renderGroups();
  });
  list.appendChild(button);
}

function renderGroupButton(list, group, nested = false) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `group-button${nested ? " nested" : ""}${state.selectedGroupId === group.id ? " active" : ""}`;
    button.innerHTML = `${group.name}<span class="count">${group.member_count}명</span>`;
    button.addEventListener("click", async () => {
      if (state.selectedGroupId === group.id) {
        state.selectedGroupId = null;
        state.selectedPeople.clear();
      } else {
        state.selectedGroupId = group.id;
        state.selectedPeople = new Set(group.members);
        promotePeople(group.members);
      }
      renderGroups();
      renderSelectionLists(true);
      updateSelectionCopy();
      closeMobileSidebar();
      await loadEvents();
    });
    list.appendChild(button);
}

function renderSelectionLists(forceRebuild = false) {
  const keyword = document.getElementById("selection-search").value.trim().toLowerCase();
  if (forceRebuild || keyword !== state.lastSearchKeyword || !state.visiblePersonEmails || !state.visibleProjectCodes) {
    state.lastSearchKeyword = keyword;
    state.visiblePersonEmails = visiblePeopleForKeyword(keyword).map((person) => person.email);
    state.visibleProjectCodes = visibleProjectsForKeyword(keyword).map((project) => project.code);
  }
  renderPeople();
  renderProjects();
}

function visiblePeopleForKeyword(keyword) {
  const personByEmail = new Map(state.config.people.map((person) => [person.email, person]));
  return ensurePersonOrder()
    .map((email) => personByEmail.get(email))
    .filter(Boolean)
    .filter((person) => {
      if (state.selectedPeople.has(person.email)) return true;
      if (!keyword) return true;
      return person.name.toLowerCase().includes(keyword) || person.email.toLowerCase().includes(keyword);
    });
}

function visibleProjectsForKeyword(keyword) {
  const projects = state.config.projects || [];
  return projects.filter((project) => {
    if (state.selectedProjects.has(project.code)) return true;
    if (!keyword) return false;
    return [project.code, project.pm, project.name, project.label].some((value) =>
      String(value || "").toLowerCase().includes(keyword)
    );
  });
}

function renderPeople() {
  const list = document.getElementById("person-list");
  list.innerHTML = "";

  const personByEmail = new Map(state.config.people.map((person) => [person.email, person]));
  (state.visiblePersonEmails || initialPersonOrder(state.config.people))
    .map((email) => personByEmail.get(email))
    .filter(Boolean)
    .forEach((person) => {
      const row = document.createElement("label");
      row.className = `person-row${state.selectedPeople.has(person.email) ? " active" : ""}`;

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.selectedPeople.has(person.email);
      checkbox.addEventListener("change", async () => {
        state.selectedGroupId = null;
        if (checkbox.checked) {
          state.selectedPeople.add(person.email);
        } else {
          state.selectedPeople.delete(person.email);
        }
        renderGroups();
        renderSelectionLists(false);
        updateSelectionCopy();
        closeMobileSidebar();
        await loadEvents();
      });

      const meta = document.createElement("div");
      meta.className = "person-meta";
      const issue = personIssue(person.email);
      const issueDetail = personIssueDetail(person.email);
      meta.innerHTML = `
        <div class="person-name-line">
          <div class="person-name">${person.name}</div>
          ${issue ? `<button type="button" class="person-error" data-email="${person.email}">${issue}</button>` : ""}
        </div>
      `;

      const errorButton = meta.querySelector(".person-error");
      if (errorButton) {
        errorButton.title = issueDetail;
        errorButton.setAttribute("aria-label", `${person.name} 연동 이슈: ${issueDetail}`);
        errorButton.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          toggleDiagnostics(person.email);
        });
      }
      row.appendChild(checkbox);
      row.appendChild(meta);
      list.appendChild(row);
    });
}

function renderProjects() {
  const panel = document.getElementById("project-panel");
  const list = document.getElementById("project-list");
  const projects = state.config.projects || [];
  const projectByCode = new Map(projects.map((project) => [project.code, project]));
  const visibleCodes = state.visibleProjectCodes || [];
  panel.hidden = visibleCodes.length === 0;
  list.innerHTML = "";
  visibleCodes
    .map((code) => projectByCode.get(code))
    .filter(Boolean)
    .forEach((project) => {
      const row = document.createElement("label");
      row.className = `project-row${state.selectedProjects.has(project.code) ? " active" : ""}`;

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.selectedProjects.has(project.code);
      checkbox.addEventListener("change", async () => {
        if (checkbox.checked) {
          state.selectedProjects.add(project.code);
        } else {
          state.selectedProjects.delete(project.code);
        }
        renderSelectionLists(false);
        updateSelectionCopy();
        closeMobileSidebar();
        await loadEvents();
      });

      const meta = document.createElement("div");
      meta.className = "project-meta";
      meta.innerHTML = `
        <div class="project-code">${project.code}</div>
        <div class="project-name">${project.pm ? `${project.pm} · ` : ""}${project.name || "과제명 없음"}</div>
      `;
      row.appendChild(checkbox);
      row.appendChild(meta);
      list.appendChild(row);
    });
}

function initialPersonOrder(people) {
  return [...people]
    .sort((left, right) => left.name.localeCompare(right.name, "ko-KR") || left.email.localeCompare(right.email))
    .map((person) => person.email);
}

function ensurePersonOrder() {
  const knownEmails = new Set(state.config.people.map((person) => person.email));
  if (!state.personOrderEmails) {
    state.personOrderEmails = initialPersonOrder(state.config.people);
    return state.personOrderEmails;
  }
  const ordered = state.personOrderEmails.filter((email) => knownEmails.has(email));
  const orderedSet = new Set(ordered);
  const missingPeople = state.config.people.filter((person) => !orderedSet.has(person.email));
  state.personOrderEmails = [...ordered, ...initialPersonOrder(missingPeople)];
  return state.personOrderEmails;
}

function promotePeople(emails) {
  const selected = new Set(emails);
  const currentOrder = ensurePersonOrder();
  state.personOrderEmails = [
    ...currentOrder.filter((email) => selected.has(email)),
    ...currentOrder.filter((email) => !selected.has(email)),
  ];
}

function renderTypeFilters() {
  const container = document.getElementById("type-filters");
  container.innerHTML = "";
  Object.entries(typeLabels).forEach(([typeId, label]) => {
    const item = document.createElement("label");
    item.className = `type-chip type-chip-${typeId}`;
    item.innerHTML = `<input type="checkbox" ${state.selectedTypes.has(typeId) ? "checked" : ""} /><span>${label}</span>`;
    item.querySelector("input").addEventListener("change", async (event) => {
      if (event.target.checked) {
        state.selectedTypes.add(typeId);
      } else {
        state.selectedTypes.delete(typeId);
      }
      await loadEvents();
    });
    container.appendChild(item);
  });
}

function updateSelectionCopy() {
  const titleNode = document.getElementById("selection-title");
  const subtitleNode = document.getElementById("selection-subtitle");
  const summaryNode = document.getElementById("selection-summary");
  const countNode = document.getElementById("selection-count");
  const detailNode = document.getElementById("selection-detail");
  const clearButton = document.getElementById("clear-selection-sidebar");
  const syncNode = document.getElementById("sync-status");
  const selectedPeople = state.config.people.filter((person) => state.selectedPeople.has(person.email));
  const selectedProjectCount = state.selectedProjects.size;

  if (state.selectedGroupId) {
    const group = state.config.groups.find((entry) => entry.id === state.selectedGroupId);
    titleNode.textContent = group ? group.name : "선택 없음";
    subtitleNode.textContent = "";
    summaryNode.hidden = false;
    countNode.textContent = `${selectedPeople.length}명 선택${selectedProjectCount ? ` / 과제 ${selectedProjectCount}개` : ""}`;
    detailNode.textContent = selectedProjectCount
      ? `${selectedPeople.length}명과 선택 과제에 연결된 일정을 조회합니다.`
      : `${selectedPeople.length}명의 공용 캘린더를 조회합니다.`;
    clearButton.hidden = false;
    syncNode.hidden = false;
    return;
  }
  if (selectedPeople.length > 0 || selectedProjectCount > 0) {
    titleNode.textContent = selectedPeople.length > 0 ? "사용자 선택" : "과제 선택";
    subtitleNode.textContent = "";
    summaryNode.hidden = false;
    countNode.textContent = `${selectedPeople.length}명 선택${selectedProjectCount ? ` / 과제 ${selectedProjectCount}개` : ""}`;
    detailNode.textContent = selectedProjectCount
      ? "선택한 사용자 일정과 과제 연결 일정을 함께 조회합니다."
      : `${selectedPeople.length}명의 공용 캘린더를 조회합니다.`;
    clearButton.hidden = false;
    syncNode.hidden = false;
    return;
  }
  titleNode.textContent = "선택 없음";
  subtitleNode.textContent = "";
  summaryNode.hidden = false;
  countNode.textContent = "좌측에서 그룹 또는 연구직을 선택하세요.";
  detailNode.textContent = "";
  clearButton.hidden = true;
  syncNode.hidden = false;
  return;
}

function renderDiagnostics(payload) {
  const node = document.getElementById("diagnostics");
  if (!node) return;
  if (!state.diagnosticsFilterEmail) {
    node.hidden = true;
    node.innerHTML = "";
    return;
  }
  const groups = payload.groups || [];
  const people = payload.people || [];
  const missingPeople = people.filter((person) => person.missing_types.length > 0);
  const target = people.find((person) => person.email === state.diagnosticsFilterEmail);

  const groupLines = groups
    .map(
      (group) => `
        <div class="diagnostics-row">
          <span>${group.name}</span>
          <span>${group.ready_people}/${group.member_count}</span>
        </div>
      `
    )
    .join("");

  const missingLines = missingPeople.length
    ? missingPeople
        .slice(0, 8)
        .map(
          (person) => `
            <div class="diagnostics-row">
              <span>${person.name}</span>
              <span>${person.binding_error ? "계정/권한 확인" : person.missing_types.join(", ")}</span>
            </div>
          `
        )
        .join("")
    : `<div class="diagnostics-row"><span>누락 없음</span><span>OK</span></div>`;

  const targetLines = target
    ? `
      <section class="diagnostics-card">
        <div class="diagnostics-title">${target.name} 점검</div>
        <div class="diagnostics-row">
          <span>이메일</span>
          <span>${target.email}</span>
        </div>
        <div class="diagnostics-row">
          <span>상태</span>
          <span>${target.binding_error ? "계정/권한 확인 필요" : target.missing_types.join(", ") || "정상"}</span>
        </div>
      </section>
    `
    : "";

  node.hidden = false;
  node.innerHTML = `
    ${targetLines}
    <section class="diagnostics-card">
      <div class="diagnostics-title">그룹 준비 상태</div>
      ${groupLines}
    </section>
    <section class="diagnostics-card">
      <div class="diagnostics-title">캘린더 누락 점검</div>
      ${missingLines}
    </section>
  `;
}

function toggleDiagnostics(email) {
  state.diagnosticsFilterEmail = state.diagnosticsFilterEmail === email ? null : email;
  renderDiagnostics(state.diagnostics || { groups: [], people: [] });
}

function personIssue(email) {
  const people = state.diagnostics?.people || [];
  const person = people.find((entry) => entry.email === email);
  if (!person) return "";
  if (person.binding_error) return "오류";
  if (person.missing_types?.length) return "누락";
  return "";
}

function personIssueDetail(email) {
  const people = state.diagnostics?.people || [];
  const person = people.find((entry) => entry.email === email);
  if (!person) return "";
  if (person.binding_error) return `계정/권한 확인 필요: ${person.binding_error}`;
  if (person.missing_types?.length) return `누락 캘린더: ${person.missing_types.join(", ")}`;
  return "";
}

async function refreshSetupState() {
  try {
    const response = await fetch("/api/config");
    const payload = await response.json();
    if (response.ok) {
      state.config = payload;
      renderSetupState(payload.setup);
    }
  } catch (_error) {
    // Ignore status refresh failures to avoid breaking the calendar layout.
  }
}

function renderWeekLabel() {
  const viewedDate = state.viewMode === "day" ? state.selectedDate : state.weekStart;
  const viewedWeekNumber = getWeekNumber(viewedDate);
  const currentWeekNumber = getWeekNumber(new Date());
  if (state.viewMode === "day") {
    document.getElementById("week-label").textContent = `${formatDateLabel(state.selectedDate)} ${weekdayLabel(state.selectedDate)} (${viewedWeekNumber}W)`;
    document.getElementById("today-button").textContent = "오늘";
    return;
  }
  const start = state.weekStart;
  const end = addDays(start, 6);
  document.getElementById("week-label").textContent = `${formatDateLabel(start)} - ${formatDateLabel(end)} (${viewedWeekNumber}W)`;
  document.getElementById("today-button").textContent = `이번 주 (${currentWeekNumber}W)`;
}

function renderCalendar(events, options = {}) {
  state.lastRenderedWeeks = events;
  const header = document.getElementById("calendar-header");
  const grid = document.getElementById("calendar-grid");
  header.innerHTML = "";
  grid.innerHTML = "";
  if (state.viewMode === "day") {
    header.appendChild(renderDayHeader(state.selectedDate, events));
    grid.appendChild(renderDayBody(state.selectedDate, events));
  } else {
    header.appendChild(renderWeekHeaderStrip(state.weekStart, events));
    grid.appendChild(renderWeekBodyStrip(state.weekStart, events));
  }
  if (typeof options.scrollTop === "number") {
    const shell = document.querySelector(".calendar-shell");
    if (shell) shell.scrollTop = options.scrollTop;
  } else {
    scrollToActiveTime();
  }
}

function renderWeekHeaderStrip(weekStart, events) {
  const strip = document.createElement("div");
  strip.className = "week-header-strip";
  strip.appendChild(renderWeekHeader(addDays(weekStart, -7), filterEventsForRange(events, addDays(weekStart, -7), weekStart)));
  strip.appendChild(renderWeekHeader(weekStart, filterEventsForRange(events, weekStart, addDays(weekStart, 7))));
  strip.appendChild(renderWeekHeader(addDays(weekStart, 7), filterEventsForRange(events, addDays(weekStart, 7), addDays(weekStart, 14))));
  return strip;
}

function renderWeekHeader(weekStart, events) {
  const header = document.createElement("section");
  header.className = "week-header-grid";

  for (let dayOffset = 0; dayOffset < 7; dayOffset += 1) {
    const day = addDays(weekStart, dayOffset);
    header.appendChild(renderDayHeaderColumn(day, events, dayOffset));
  }
  return header;
}

function renderDayHeader(date, events) {
  const header = document.createElement("section");
  header.className = "day-header-grid";
  header.appendChild(renderDayHeaderColumn(date, events, 0, false));
  return header;
}

function renderDayHeaderColumn(day, events, dayOffset = 0, enableDayClick = true) {
  const now = new Date();
  const column = document.createElement("section");
  column.className = `day-header-card${dayOffset === 0 ? " is-first" : ""}${dayOffset === 6 ? " is-last" : ""}${isWeekend(day) ? " is-weekend" : ""}`;
  const isToday = sameDay(day, now);

  const head = document.createElement("div");
  head.className = "day-head";
  head.innerHTML = `
    <div class="day-label">${weekdayLabel(day)}</div>
    <div class="day-date${isToday ? " today" : ""}">${day.getDate()}</div>
  `;
  if (enableDayClick) {
    head.classList.add("clickable");
    head.addEventListener("click", async () => {
      state.viewMode = "day";
      state.selectedDate = startOfDay(day);
      state.weekStart = startOfView(day);
      document.getElementById("view-mode").value = "day";
      await loadEvents(0, { scrollTop: document.querySelector(".calendar-shell")?.scrollTop || 0 });
    });
  }
  column.appendChild(head);

  const allDayStrip = document.createElement("div");
  allDayStrip.className = "all-day-strip";
  const allDayEvents = events.filter((event) => event.allDay && eventCoversDay(event, day));
  if (allDayEvents.length === 0) {
    allDayStrip.innerHTML = `<div class="all-day-label">종일 일정 없음</div>`;
  } else {
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "all-day-more";
    if (allDayEvents.some((event) => event.calendarType === "important")) {
      toggle.classList.add("has-important");
    }
    toggle.textContent = `${toggle.classList.contains("has-important") ? "! " : ""}종일 ${allDayEvents.length}`;
    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      showAllDayPreview(allDayEvents, event);
    });
    if (window.matchMedia("(hover: hover)").matches) {
      toggle.addEventListener("mouseenter", (event) => showAllDayPreview(allDayEvents, event));
      toggle.addEventListener("mousemove", (event) => moveHoverPreview(event));
      toggle.addEventListener("mouseleave", hideHoverPreview);
    }
    allDayStrip.appendChild(toggle);
  }
  column.appendChild(allDayStrip);
  return column;
}

function renderWeekBody(weekStart, events) {
  const panel = document.createElement("section");
  panel.className = "week-body-grid";

  for (let dayOffset = 0; dayOffset < 7; dayOffset += 1) {
    const day = addDays(weekStart, dayOffset);
    panel.appendChild(renderDayBodyColumn(day, events, dayOffset));
  }
  return panel;
}

function renderDayBody(date, events) {
  const panel = document.createElement("section");
  panel.className = "day-body-grid";
  panel.appendChild(renderDayBodyColumn(date, events, 0));
  return panel;
}

function renderDayBodyColumn(day, events, dayOffset = 0) {
  const template = document.getElementById("event-template");
  const now = new Date();
  const column = document.createElement("section");
  column.className = `day-body-column${dayOffset === 0 ? " is-first" : ""}${dayOffset === 6 ? " is-last" : ""}${isWeekend(day) ? " is-weekend" : ""}`;
  const body = document.createElement("div");
  body.className = "day-events";
  const timeGrid = document.createElement("div");
  timeGrid.className = "time-grid";
  renderTimeLines(timeGrid, dayOffset === 0);
  if (sameDay(day, now)) {
    renderCurrentTimeLine(timeGrid, now);
  }

  const dayEvents = events.filter((event) => !event.allDay && sameDay(parseEventDate(event.start), day));
  const overlapMeta = buildOverlapMeta(dayEvents);
  const maxLaneCount = Math.max(1, ...Array.from(overlapMeta.values()).map((meta) => meta.laneCount || 1));
  if (state.overlapSplit && maxLaneCount > 1) {
    timeGrid.classList.add("split-overlaps");
    timeGrid.style.gridTemplateColumns = dayOffset === 0 ? `30px repeat(${maxLaneCount}, minmax(0, 1fr))` : `repeat(${maxLaneCount}, minmax(0, 1fr))`;
  }

  if (dayEvents.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-day";
    empty.textContent = "일정 없음";
    timeGrid.appendChild(empty);
  } else {
    dayEvents.forEach((event) => {
      const node = template.content.firstElementChild.cloneNode(true);
      node.classList.add("timeline-event");
      if (event.mondayLinked) {
        node.classList.add("monday-linked");
        node.setAttribute("aria-label", "먼데이 연동 일정");
      }
      if (event.calendarType === "important") {
        node.classList.add("important-event");
      }
      applyEventTheme(node, event.color);
      if (isTentativeTitle(event.title)) {
        node.style.opacity = "0.3";
      }
      const overlap = overlapMeta.get(event) || { overlapCount: 0, stackIndex: 0 };
      if (state.overlapSplit && maxLaneCount > 1) {
        node.classList.add("overlap-split");
        const firstLane = dayOffset === 0 ? 2 : 1;
        if (overlap.laneCount > 1) {
          node.style.gridColumn = `${firstLane + overlap.stackIndex} / span 1`;
        } else {
          node.style.gridColumn = `${firstLane} / span ${maxLaneCount}`;
        }
      } else if (overlap.overlapCount) {
        node.classList.add("overlap-stack");
        const visualStackIndex = Math.min(9, overlap.stackIndex);
        node.style.setProperty("--stack-shift-x", `${visualStackIndex}px`);
        node.style.setProperty("--stack-shift-y", `${Math.min(8, visualStackIndex * 3)}px`);
      }
      node.querySelector(".event-person").textContent = event.personName;
      node.querySelector(".event-type").textContent = eventTypeLabel(event);
      node.querySelector(".event-title").textContent = event.title;
      node.querySelector(".event-time").textContent = `${formatTime(parseEventDate(event.start))} - ${formatTime(parseEventDate(event.end))}`;
      node.querySelector(".event-location").textContent = event.location || "";
      bindEventHover(node, event);
      placeTimedEvent(node, event);
      timeGrid.appendChild(node);
    });
  }

  body.appendChild(timeGrid);
  column.appendChild(body);
  return column;
}

function renderWeekBodyStrip(weekStart, events) {
  const strip = document.createElement("div");
  strip.className = "week-body-strip";
  strip.appendChild(renderWeekBody(addDays(weekStart, -7), filterEventsForRange(events, addDays(weekStart, -7), weekStart)));
  strip.appendChild(renderWeekBody(weekStart, filterEventsForRange(events, weekStart, addDays(weekStart, 7))));
  strip.appendChild(renderWeekBody(addDays(weekStart, 7), filterEventsForRange(events, addDays(weekStart, 7), addDays(weekStart, 14))));
  return strip;
}

function filterEventsForRange(events, start, end) {
  return events.filter((event) => {
    const eventStart = parseEventDate(event.start);
    const eventEnd = parseEventDate(event.end || event.start);
    return eventStart < end && eventEnd > start;
  });
}

function eventCoversDay(event, day) {
  const dayStart = startOfDay(day);
  const dayEnd = addDays(dayStart, 1);
  const eventStart = startOfDay(parseEventDate(event.start));
  const eventEnd = startOfDay(parseEventDate(event.end || event.start));
  return eventStart < dayEnd && eventEnd > dayStart;
}

function renderTimeLines(container, showLabels = false) {
  const hourHeight = getHourRowHeight();
  if (showLabels) {
    container.classList.add("has-time-labels");
  }
  for (let hour = 0; hour < 24; hour += 1) {
    const top = hour * hourHeight;

    const line = document.createElement("div");
    line.className = "time-line";
    line.style.top = `${top}px`;
    container.appendChild(line);
    if (showLabels) {
      const label = document.createElement("div");
      label.className = "time-label";
      label.style.top = `${top}px`;
      label.textContent = String(hour).padStart(2, "0");
      container.appendChild(label);
    }
  }
}

function renderCurrentTimeLine(container, now) {
  const hourHeight = getHourRowHeight();
  const minutes = now.getHours() * 60 + now.getMinutes();
  const top = minutes * (hourHeight / 60);

  const line = document.createElement("div");
  line.className = "current-time-line";
  line.style.top = `${top}px`;
  container.appendChild(line);

  const dot = document.createElement("div");
  dot.className = "current-time-dot";
  dot.style.top = `${top}px`;
  container.appendChild(dot);
}

function placeTimedEvent(node, event) {
  const start = parseEventDate(event.start);
  const end = parseEventDate(event.end);
  const startMinutes = start.getHours() * 60 + start.getMinutes();
  let endMinutes = end.getHours() * 60 + end.getMinutes();
  if (!Number.isFinite(endMinutes) || endMinutes <= startMinutes) {
    endMinutes = startMinutes + 30;
  }

  const slotMinutes = 5;
  const minDuration = 30;
  const safeEndMinutes = Math.max(endMinutes, startMinutes + minDuration);
  const rowStart = Math.floor(startMinutes / slotMinutes) + 1;
  const rowEnd = Math.ceil(safeEndMinutes / slotMinutes) + 1;

  node.style.gridRow = `${rowStart} / ${Math.max(rowStart + 1, rowEnd)}`;
  node.style.height = "auto";
  node.style.top = "auto";
}

function buildOverlapMeta(events) {
  const meta = new Map(events.map((event) => [event, { overlapCount: 0, stackIndex: 0, laneCount: 1 }]));
  const adjacency = new Map(events.map((event) => [event, new Set()]));
  for (let i = 0; i < events.length; i += 1) {
    for (let j = i + 1; j < events.length; j += 1) {
      if (!eventsOverlap(events[i], events[j])) continue;
      adjacency.get(events[i]).add(events[j]);
      adjacency.get(events[j]).add(events[i]);
      meta.get(events[i]).overlapCount += 1;
      meta.get(events[j]).overlapCount += 1;
    }
  }
  const visited = new Set();
  events.forEach((event) => {
    if (visited.has(event)) return;
    const group = [];
    const queue = [event];
    visited.add(event);
    while (queue.length) {
      const current = queue.shift();
      group.push(current);
      adjacency.get(current).forEach((next) => {
        if (visited.has(next)) return;
        visited.add(next);
        queue.push(next);
      });
    }
    assignOverlapLanes(group, meta);
  });
  return meta;
}

function assignOverlapLanes(events, meta) {
  const sorted = [...events].sort((a, b) => parseEventDate(a.start) - parseEventDate(b.start) || parseEventDate(a.end) - parseEventDate(b.end));
  const active = [];
  let laneCount = 1;
  sorted.forEach((event) => {
    const eventStart = parseEventDate(event.start).getTime();
    const eventEnd = parseEventDate(event.end).getTime();
    for (let i = active.length - 1; i >= 0; i -= 1) {
      if (active[i].end <= eventStart) active.splice(i, 1);
    }
    const used = new Set(active.map((entry) => entry.stackIndex));
    let stackIndex = 0;
    while (used.has(stackIndex)) stackIndex += 1;
    meta.get(event).stackIndex = stackIndex;
    active.push({ end: eventEnd, stackIndex, event });
    laneCount = Math.max(laneCount, active.length, stackIndex + 1);
  });
  events.forEach((event) => {
    meta.get(event).laneCount = laneCount;
  });
}

function eventsOverlap(left, right) {
  const leftStart = parseEventDate(left.start).getTime();
  const leftEnd = parseEventDate(left.end).getTime();
  const rightStart = parseEventDate(right.start).getTime();
  const rightEnd = parseEventDate(right.end).getTime();
  return leftStart < rightEnd && rightStart < leftEnd;
}

function isTentativeTitle(title) {
  const text = String(title || "").trim();
  if (!text) return false;
  return /^(?:tbd|tba)/i.test(text) || /(?:^|[\s([【\[])(?:tbd|tba)(?:$|[\s)\]】.,:;!?])/i.test(text) || /(?:tbd|tba)\s*$/i.test(text) || text.includes("예정");
}

function applyEventTheme(node, color) {
  const fg = getContrastColor(color);
  node.style.setProperty("--event-color", color);
  node.style.setProperty("--event-bg", color);
  node.style.setProperty("--event-fg", fg);
  node.style.setProperty("--event-fg-muted", fg === "#ffffff" ? "rgba(255,255,255,0.86)" : "rgba(24,33,47,0.72)");
}

function bindEventHover(node, event) {
  if (!window.matchMedia("(hover: hover)").matches) return;
  node.addEventListener("mouseenter", (mouseEvent) => showHoverPreview(event, mouseEvent));
  node.addEventListener("mousemove", (mouseEvent) => moveHoverPreview(mouseEvent));
  node.addEventListener("mouseleave", hideHoverPreview);
}

function showHoverPreview(event, mouseEvent) {
  const preview = document.getElementById("hover-preview");
  if (!preview) return;
  preview.innerHTML = `
    <div class="hover-preview-person">${event.personName}</div>
    <div class="hover-preview-title">${event.title}</div>
    <div class="hover-preview-meta">${event.allDay ? "종일" : `${formatTime(parseEventDate(event.start))} - ${formatTime(parseEventDate(event.end))}`}</div>
    ${event.location ? `<div class="hover-preview-meta">${event.location}</div>` : ""}
    <div class="hover-preview-type">${eventTypeLabel(event)}</div>
  `;
  preview.hidden = false;
  moveHoverPreview(mouseEvent);
}

function eventTypeLabel(event) {
  if (event.calendarType === "important") {
    return event.calendarTypeLabel || "제주연구원";
  }
  return typeLabels[event.calendarType] || event.calendarTypeLabel;
}

function showAllDayPreview(events, mouseEvent) {
  const preview = document.getElementById("hover-preview");
  if (!preview) return;
  preview.innerHTML = `
    <div class="hover-preview-person">종일 일정 ${events.length}건</div>
    <div class="hover-preview-list">
      ${events
        .map(
          (event) => `
            <div class="hover-preview-list-item">
              <span class="hover-preview-dot" style="background:${event.color || "#2357d6"}"></span>
              <span>${event.personName} ${event.title}</span>
            </div>
          `
        )
        .join("")}
    </div>
  `;
  preview.hidden = false;
  moveHoverPreview(mouseEvent);
}

function moveHoverPreview(mouseEvent) {
  const preview = document.getElementById("hover-preview");
  if (!preview || preview.hidden) return;
  const margin = 18;
  const offsetX = 18;
  const offsetY = 14;
  const width = preview.offsetWidth || 240;
  const height = preview.offsetHeight || 120;
  let left = mouseEvent.clientX + offsetX;
  let top = mouseEvent.clientY + offsetY;
  if (left + width + margin > window.innerWidth) {
    left = mouseEvent.clientX - width - offsetX;
  }
  if (top + height + margin > window.innerHeight) {
    top = mouseEvent.clientY - height - offsetY;
  }
  preview.style.left = `${Math.max(margin, left)}px`;
  preview.style.top = `${Math.max(margin, top)}px`;
}

function hideHoverPreview() {
  const preview = document.getElementById("hover-preview");
  if (!preview) return;
  preview.hidden = true;
}

function toggleAllDay(dayKey) {
  if (state.expandedAllDayDays.has(dayKey)) {
    state.expandedAllDayDays.delete(dayKey);
  } else {
    state.expandedAllDayDays = new Set([dayKey]);
  }
  renderCalendar(state.lastRenderedWeeks || []);
}

function startOfDay(date) {
  const copy = new Date(date);
  copy.setHours(0, 0, 0, 0);
  return copy;
}

function isWeekend(date) {
  const day = date.getDay();
  return day === 0 || day === 6;
}

function formatDayKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function bindGlobalCollapse() {
  document.addEventListener("click", (event) => {
    if (state.expandedAllDayDays.size === 0) return;
    if (event.target.closest(".all-day-strip")) return;
    collapseAllDay();
  });

  const shell = document.querySelector(".calendar-shell");
  if (shell) {
    shell.addEventListener(
      "scroll",
      () => {
        if (state.expandedAllDayDays.size === 0) return;
        collapseAllDay();
      },
      { passive: true }
    );
  }
}

function collapseAllDay() {
  if (state.expandedAllDayDays.size === 0) return;
  state.expandedAllDayDays.clear();
  renderCalendar(state.lastRenderedWeeks || []);
}

function renderError(message) {
  renderCalendar([]);
  const grid = document.getElementById("calendar-grid");
  const firstDay = grid.querySelector(".day-body-column .day-events .time-grid");
  if (!firstDay) return;
  const empty = firstDay.querySelector(".empty-day");
  if (empty) {
    empty.textContent = message;
  }
}

async function shiftView(direction) {
  if (state.viewMode === "day") {
    const shell = document.querySelector(".calendar-shell");
    const preservedScrollTop = shell ? shell.scrollTop : null;
    state.selectedDate = addDays(state.selectedDate, direction);
    state.weekStart = startOfView(state.selectedDate);
    await loadEvents(direction, { scrollTop: preservedScrollTop });
    return;
  }
  await shiftWeek(direction * 7, direction > 0 ? -1 : 1);
}

async function shiftWeek(days, direction = 0) {
  const shell = document.querySelector(".calendar-shell");
  const preservedScrollTop = shell ? shell.scrollTop : null;
  await animateWeekTransition(direction);
  state.weekStart = addDays(state.weekStart, days);
  await loadEvents(direction, { scrollTop: preservedScrollTop });
}

function clearSelection() {
  state.selectedGroupId = null;
  state.selectedPeople.clear();
  state.selectedProjects.clear();
  state.personOrderEmails = initialPersonOrder(state.config.people);
  state.visiblePersonEmails = null;
  state.visibleProjectCodes = null;
  renderGroups();
  renderSelectionLists(true);
  updateSelectionCopy();
  closeMobileSidebar();
}

function startOfView(date) {
  const copy = new Date(date);
  copy.setHours(0, 0, 0, 0);
  copy.setDate(copy.getDate() - copy.getDay());
  return copy;
}

function addDays(date, days) {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + days);
  return copy;
}

function weekdayLabel(date) {
  return date.toLocaleDateString("ko-KR", { weekday: "short" });
}

function formatDateLabel(date) {
  return date.toLocaleDateString("ko-KR", { month: "short", day: "numeric" });
}

function formatTime(date) {
  return date.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
}

function formatDateTime(date) {
  return date.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function getWeekNumber(date) {
  const target = new Date(date);
  target.setHours(0, 0, 0, 0);
  target.setDate(target.getDate() + 3 - ((target.getDay() + 6) % 7));
  const firstThursday = new Date(target.getFullYear(), 0, 4);
  firstThursday.setDate(firstThursday.getDate() + 3 - ((firstThursday.getDay() + 6) % 7));
  const diff = target.getTime() - firstThursday.getTime();
  return 1 + Math.round(diff / 604800000);
}

function getContrastColor(hex) {
  const value = (hex || "").replace("#", "");
  if (!/^[0-9a-fA-F]{6}$/.test(value)) return "#18212f";
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return luminance < 0.56 ? "#ffffff" : "#18212f";
}

function parseEventDate(value) {
  if (!value) return new Date();
  return new Date(value);
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function toggleMobileSidebar() {
  state.isMobileSidebarOpen = !state.isMobileSidebarOpen;
  syncMobileChrome();
}

function closeMobileSidebar() {
  if (!state.isMobileSidebarOpen) return;
  state.isMobileSidebarOpen = false;
  syncMobileChrome();
}

function syncMobileChrome() {
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("sidebar-backdrop");
  const mobile = window.innerWidth <= 1100;
  sidebar.classList.toggle("mobile-open", mobile && state.isMobileSidebarOpen);
  backdrop.hidden = !(mobile && state.isMobileSidebarOpen);
  document.body.classList.toggle("sidebar-open", mobile && state.isMobileSidebarOpen);
}

function syncPlatformChrome() {
  const ua = navigator.userAgent || "";
  const isIOS = /iPhone|iPad|iPod/.test(ua);
  const isWebKit = /WebKit/.test(ua);
  const isSafari = /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS/.test(ua);
  document.documentElement.classList.toggle("ios-safari", isIOS && isWebKit && isSafari);
}

function bindCalendarGestures() {
  const shell = document.querySelector(".calendar-shell");
  if (!shell) return;

  let touchStartX = 0;
  let touchStartY = 0;
  let dragging = false;
  let gestureMode = "pending";

  function activeStrips() {
    return document.querySelectorAll(".week-header-strip, .week-body-strip");
  }

  function applyDrag(deltaX) {
    activeStrips().forEach((strip) => {
      strip.style.transition = "";
      strip.style.transform = `translateX(calc(-33.333333% + ${deltaX}px))`;
    });
  }

  function resetDrag(animated = true) {
    const strips = activeStrips();
    if (!strips.length) return;
    strips.forEach((strip) => {
      if (animated) {
        strip.style.transition = "transform 1000ms cubic-bezier(0.22, 1, 0.36, 1)";
      }
      strip.style.transform = "translateX(-33.333333%)";
    });
    window.setTimeout(() => {
      activeStrips().forEach((strip) => {
        strip.style.transition = "";
      });
    }, 1040);
  }

  shell.addEventListener(
    "touchstart",
    (event) => {
      hideHoverPreview();
      const touch = event.changedTouches[0];
      touchStartX = touch.clientX;
      touchStartY = touch.clientY;
      dragging = true;
      gestureMode = "pending";
      activeStrips().forEach((strip) => {
        strip.style.transition = "";
        strip.style.transform = "translateX(-33.333333%)";
      });
    },
    { passive: true }
  );

  shell.addEventListener(
    "touchmove",
    (event) => {
      if (!dragging) return;
      const touch = event.changedTouches[0];
      const deltaX = touch.clientX - touchStartX;
      const deltaY = touch.clientY - touchStartY;
      if (gestureMode === "pending") {
        if (Math.abs(deltaX) < 8 && Math.abs(deltaY) < 8) return;
        gestureMode = Math.abs(deltaX) > Math.abs(deltaY) ? "horizontal" : "vertical";
      }
      hideHoverPreview();
      if (gestureMode !== "horizontal") return;
      event.preventDefault();
      applyDrag(deltaX);
    },
    { passive: false }
  );

  shell.addEventListener(
    "touchend",
    async (event) => {
      if (!dragging) return;
      dragging = false;
      const touch = event.changedTouches[0];
      const deltaX = touch.clientX - touchStartX;
      const deltaY = touch.clientY - touchStartY;
      if (gestureMode !== "horizontal" || Math.abs(deltaX) < 48 || Math.abs(deltaX) <= Math.abs(deltaY)) {
        resetDrag(true);
        return;
      }
      await shiftView(deltaX < 0 ? 1 : -1);
    },
    { passive: true }
  );

  shell.addEventListener(
    "touchcancel",
    () => {
      dragging = false;
      gestureMode = "pending";
      resetDrag(true);
    },
    { passive: true }
  );
}

function scrollToActiveTime() {
  const shell = document.querySelector(".calendar-shell");
  if (!shell) return;
  const header = document.getElementById("calendar-header");
  const now = new Date();
  const hourHeight = getHourRowHeight();
  const isMobile = window.innerWidth <= 720;
  const baseDate = state.viewMode === "day" ? state.selectedDate : state.weekStart;
  const inViewedRange = state.viewMode === "day" ? sameDay(baseDate, now) : sameDay(baseDate, now) || (now >= baseDate && now < addDays(baseDate, 7));
  const baseMinutes = isMobile ? 8 * 60 : (inViewedRange ? now.getHours() * 60 + now.getMinutes() : 9 * 60);
  const stickyOffset = header ? header.offsetHeight : 0;
  const topPadding = window.innerWidth <= 720 ? 20 : 12;
  const target = Math.max(0, baseMinutes * (hourHeight / 60) - stickyOffset + topPadding);
  shell.scrollTop = target;
}

function getHourRowHeight() {
  const value = getComputedStyle(document.documentElement).getPropertyValue("--hour-row-height").trim();
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 40;
}

async function animateWeekTransition(direction) {
  if (state.viewMode === "day") return;
  const strips = document.querySelectorAll(".week-header-strip, .week-body-strip");
  if (!strips.length || !direction) return;
  strips.forEach((strip) => {
    strip.style.transition = "transform 1000ms cubic-bezier(0.22, 1, 0.36, 1)";
    strip.style.transform = direction > 0 ? "translateX(0%)" : "translateX(-66.666667%)";
  });
  await new Promise((resolve) => window.setTimeout(resolve, 1000));
}

async function loadEvents(direction = 0, options = {}) {
  renderWeekLabel();
  if (!state.config) return;
  refreshSetupState();
  loadDiagnostics();

  if (state.selectedPeople.size === 0 && state.selectedProjects.size === 0 && !state.selectedTypes.has("important")) {
    renderCalendar([], options);
    return;
  }

  try {
    const params = new URLSearchParams();
    params.set("emails", Array.from(state.selectedPeople).join(","));
    params.set("project_codes", Array.from(state.selectedProjects).join(","));
    params.set("types", Array.from(state.selectedTypes).join(","));
    const queryWeekStart = state.viewMode === "day" ? startOfView(state.selectedDate) : state.weekStart;
    params.set("start", addDays(queryWeekStart, -7).toISOString());
    params.set("end", addDays(queryWeekStart, 14).toISOString());

    const response = await fetch(`/api/events?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
      renderError(payload.error || "일정 조회 실패");
      return;
    }
    renderCalendar(payload.events, options);
  } catch (_error) {
    renderError("일정 조회 실패");
  }
}
