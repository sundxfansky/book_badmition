const TABS_CACHE_KEY = "badminton_booker.tabs";
const WX_TOKEN_CACHE_KEY = "badminton_booker.wx_token";

const $ = (id) => document.getElementById(id);

function syncEditableDisplay(field) {
  const input = field.querySelector("input");
  const display = field.querySelector(".editable-display");
  display.textContent = input.value;
}

function setupEditableFields() {
  for (const field of document.querySelectorAll(".editable-field")) {
    const input = field.querySelector("input");
    const display = field.querySelector(".editable-display");
    display.addEventListener("click", () => {
      display.style.display = "none";
      input.style.display = "";
      input.focus();
      input.select();
    });
    display.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        display.click();
      }
    });
    const finishEdit = () => {
      input.style.display = "none";
      display.style.display = "";
      display.textContent = input.value;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    };
    input.addEventListener("blur", finishEdit);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); input.blur(); }
      if (e.key === "Escape") { input.blur(); }
    });
    syncEditableDisplay(field);
  }
}

const tabs = [];
let activeTabId = null;

function defaultRequestCollapsed() {
  return true;
}

function generateId() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

function createTabState() {
  return {
    snapshot: null,
    params: null,
    selectedDates: [],
    selectedCells: [],
    monitorCells: [],
    siteListSnapshot: null,
    siteStatusQueried: false,
    reservedSnapshot: null,
    previewPinned: false,
    logs: [],
    requestCollapsed: defaultRequestCollapsed(),
    running: false,
    waitingForSchedule: false,
  };
}

function activeTab() {
  return tabs.find((t) => t.id === activeTabId);
}

function activeState() {
  const tab = activeTab();
  return tab ? tab.state : createTabState();
}

function apiForTab(tab, path, options = {}) {
  return fetch(path, {
    ...options,
    headers: {
      "content-type": "application/json",
      "x-client-id": tab.clientId,
      ...(options.headers || {}),
    },
  }).then((r) => {
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  });
}

function api(path, options = {}) {
  const tab = activeTab();
  if (!tab) throw new Error("No active tab");
  return apiForTab(tab, path, options);
}

// --- Tab management ---

function saveTabs() {
  try {
    const data = tabs.map((t) => ({ id: t.id, clientId: t.clientId }));
    window.localStorage?.setItem(TABS_CACHE_KEY, JSON.stringify({ tabs: data, activeTabId }));
  } catch {}
}

function loadTabs() {
  try {
    const raw = window.localStorage?.getItem(TABS_CACHE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function addTab(clientId) {
  const id = generateId();
  const tab = { id, clientId: clientId || generateId(), state: createTabState(), pollTimer: null };
  tabs.push(tab);
  saveTabs();
  return tab;
}

function switchTab(tabId) {
  if (activeTabId === tabId) return;
  saveCurrentTabUI();
  activeTabId = tabId;
  saveTabs();
  const tab = activeTab();
  if (tab) {
    restoreTabUI(tab);
    renderTabs();
    bootTab(tab);
  }
}

function saveCurrentTabUI() {
  const tab = activeTab();
  if (!tab) return;
  tab.state.selectedDates = activeState().selectedDates;
  tab.state.selectedCells = activeState().selectedCells;
  tab.state.monitorCells = activeState().monitorCells;
  tab.state.previewPinned = activeState().previewPinned;
  tab.state.requestCollapsed = activeState().requestCollapsed;
  tab.state.logs = activeState().logs;
}

function restoreTabUI(tab) {
  const s = tab.state;
  if (s.params) {
    applyParams(s.params);
  }
  renderChoices();
  renderStatus({ running: s.running, waiting_for_schedule: s.waitingForSchedule, logs: s.logs, last_request: null });
  renderRequestPanel();
}

function renderTabs() {
  const container = $("tabList");
  container.innerHTML = "";
  for (const tab of tabs) {
    const btn = document.createElement("button");
    btn.className = `tab-item${tab.id === activeTabId ? " active" : ""}`;
    const dot = document.createElement("span");
    const dotClass = tab.state.running ? "running" : tab.state.waitingForSchedule ? "waiting" : "";
    dot.className = `tab-dot${dotClass ? " " + dotClass : ""}`;
    btn.appendChild(dot);
    const label = document.createElement("span");
    label.textContent = tabSummary(tab, tabs.indexOf(tab));
    btn.appendChild(label);
    btn.addEventListener("click", () => switchTab(tab.id));
    container.appendChild(btn);
  }
  updateAddTabButton();
}

function updateAddTabButton() {
  const tab = activeTab();
  const canAdd = tab && (tab.state.running || tab.state.waitingForSchedule);
  $("addTabBtn").disabled = !canAdd;
}

function handleAddTab() {
  const tab = activeTab();
  if (!tab || (!tab.state.running && !tab.state.waitingForSchedule)) return;
  saveCurrentTabUI();
  const newTab = addTab();
  activeTabId = newTab.id;
  saveTabs();
  renderTabs();
  bootTab(newTab);
}

function switchRelativeTab(delta) {
  if (!tabs.length) return;
  saveCurrentTabUI();
  const currentIndex = Math.max(0, tabs.findIndex((t) => t.id === activeTabId));
  const nextIndex = (currentIndex + delta + tabs.length) % tabs.length;
  switchTab(tabs[nextIndex].id);
}

// --- PLACEHOLDER_PARAMS_AND_RENDER ---

function currentParams() {
  cacheWxToken();
  return {
    dry_run: $("dryRunInput").checked,
    verify_ssl: false,
    interval_seconds: Number($("intervalInput").value || 0.1),
    max_attempts: Number($("maxAttemptsInput").value || 100000),
    schedule_enabled: $("scheduleEnabledInput").checked,
    scheduled_start_at: normalizeScheduledStart($("scheduledStartInput").value),
    date: activeState().selectedDates[0] || dateFieldValue("dateInput"),
    dates: activeState().selectedDates,
    request_mode: requestMode(),
    selections: activeState().selectedCells,
    monitor_enabled: $("monitorEnabledInput").checked,
    monitor_date: activeState().selectedDates[0] || dateFieldValue("dateInput"),
    monitor_interval_seconds: Number($("monitorIntervalInput").value || 20),
    monitor_selections: activeState().monitorCells,
    headers: {
      "wx-token": $("wxTokenInput").value.trim(),
      "shop-id": $("shopIdInput").value.trim(),
      "brand-code": $("brandCodeInput").value.trim(),
    },
  };
}

function applyParams(params, options = {}) {
  const s = activeState();
  s.params = params;
  const cachedToken = getCachedWxToken();
  const importedToken = params.headers?.["wx-token"] || "";
  const token = options.preferParamsToken ? importedToken || cachedToken : cachedToken || importedToken;
  setDateField("dateInput", params.date);
  $("intervalInput").value = params.interval_seconds ?? 0.1;
  $("maxAttemptsInput").value = params.max_attempts ?? 100000;
  $("dryRunInput").checked = params.dry_run === true;
  $("scheduleEnabledInput").checked = params.schedule_enabled === true;
  $("scheduledStartInput").value = toDatetimeLocalValue(params.scheduled_start_at || "");
  $("monitorEnabledInput").checked = params.monitor_enabled === true;
  $("monitorIntervalInput").value = params.monitor_interval_seconds ?? 20;
  $("wxTokenInput").value = token;
  $("shopIdInput").value = params.headers?.["shop-id"] || "";
  $("brandCodeInput").value = params.headers?.["brand-code"] || "";
  $("pairModeInput").checked = params.request_mode === "pair";

  for (const field of document.querySelectorAll(".editable-field")) {
    const input = field.querySelector("input");
    input.style.display = "none";
    const display = field.querySelector(".editable-display");
    display.style.display = "";
    display.textContent = input.value;
  }

  for (const [inputId, displayId] of [["wxTokenInput", "wxTokenDisplay"], ["shopIdInput", "shopIdDisplay"], ["brandCodeInput", "brandCodeDisplay"]]) {
    const input = $(inputId);
    const display = $(displayId);
    if (input && display) display.textContent = input.value;
  }

  s.selectedDates = normalizeDates(params.dates || (params.date ? [params.date] : [])).slice(0, 1);
  if (s.selectedDates[0]) setDateField("dateInput", s.selectedDates[0]);
  s.selectedCells = params.selections || defaultSelections(params);
  s.monitorCells = params.monitor_selections || [];
}

// --- PLACEHOLDER_CACHE ---

function cacheWxToken() {
  const token = $("wxTokenInput").value.trim();
  if (token) {
    setCachedWxToken(token);
  } else {
    clearCachedWxToken();
  }
}

function getCachedWxToken() {
  try { return window.localStorage?.getItem(WX_TOKEN_CACHE_KEY) || ""; } catch { return ""; }
}

function setCachedWxToken(token) {
  try { window.localStorage?.setItem(WX_TOKEN_CACHE_KEY, token); } catch {}
}

function clearCachedWxToken() {
  try { window.localStorage?.removeItem(WX_TOKEN_CACHE_KEY); } catch {}
}

function defaultSelections(params) {
  const s = activeState();
  const courts = params.courts || params.fixed_courts || s.snapshot?.fixed_courts || [];
  const times = params.time_slots || params.selected_times || s.snapshot?.selected_times || [];
  const selections = [];
  for (const court of courts) {
    for (const timeSlot of normalizeTimes(times)) {
      selections.push({ court, time_slot: timeSlot });
    }
  }
  return selections;
}

function renderChoices() {
  renderSubtitle();
  renderDateMeta();
  renderScheduleGrid();
  renderTabs();
}

function renderSubtitle() {
  const s = activeState();
  if (!s.snapshot) return;
  const mode = $("monitorEnabledInput").checked
    ? `监听下单 · 已选 ${s.monitorCells.length} 个不可预约场地时间`
    : `普通抢票 · 已选 ${s.selectedCells.length} 个场地时间`;
  $("subtitle").textContent = `${s.selectedDates.length} 个日期 · ${mode}`;
}

function renderDateMeta() {
  const s = activeState();
  const date = s.selectedDates[0] || dateFieldValue("dateInput");
  $("dateWeekday").textContent = weekdayText(date);
}

// --- PLACEHOLDER_GRID ---

function renderScheduleGrid() {
  const s = activeState();
  const grid = $("scheduleGrid");
  const courts = allCourts();
  const times = allTimes();
  const monitorMode = $("monitorEnabledInput").checked;
  updateMonitorControls();
  grid.style.gridTemplateColumns = `84px repeat(${courts.length}, minmax(58px, 1fr))`;
  grid.innerHTML = "";

  grid.appendChild(cell("时间 / 场地", "schedule-head schedule-corner"));
  for (const court of courts) {
    grid.appendChild(cell(court.site_name, "schedule-head"));
  }

  for (const timeSlot of times) {
    grid.appendChild(cell(`${timeSlot.start_time}-${timeSlot.end_time}`, "schedule-time"));
    for (const court of courts) {
      const status = slotStatus(court, timeSlot);
      const selected = monitorMode ? isMonitorCell(court, timeSlot) : isSelectedCell(court, timeSlot);
      const button = document.createElement("button");
      const disabled = monitorMode && (!s.siteStatusQueried || status.available);
      button.className = `schedule-cell${selected ? " active" : ""}${status.available ? " available" : status.reserved ? " reserved" : " occupied"}${disabled ? " disabled" : ""}`;
      button.disabled = monitorMode && !s.siteStatusQueried;
      const mainText = status.owner ? status.owner : `${timeSlot.price} 元`;
      const mainClass = status.owner ? "slot-owner" : "";
      button.innerHTML = `<span class="${mainClass}">${escapeHtml(mainText)}</span><small>${escapeHtml(status.label)}</small>`;
      button.title = status.desc;
      button.addEventListener("click", () => {
        if (monitorMode) {
          if (!s.siteStatusQueried) { showNotice("请等待自动查询当前场地预约情况完成"); return; }
          if (status.available) { showNotice("这个场地当前可预约，请切换到普通抢票模式"); return; }
          toggleMonitorCell(court, timeSlot);
        } else {
          toggleCell(court, timeSlot);
        }
      });
      grid.appendChild(button);
    }
  }
}

function cell(text, className) {
  const item = document.createElement("div");
  item.className = className;
  item.textContent = text;
  return item;
}

function toggleCell(court, timeSlot) {
  const s = activeState();
  if (isSelectedCell(court, timeSlot)) {
    s.selectedCells = s.selectedCells.filter((item) => !sameCell(item, court, timeSlot));
  } else {
    s.selectedCells.push({ court, time_slot: timeSlot });
  }
  renderChoices();
  preview();
}

function toggleMonitorCell(court, timeSlot) {
  const s = activeState();
  if (isMonitorCell(court, timeSlot)) {
    s.monitorCells = s.monitorCells.filter((item) => !sameCell(item, court, timeSlot));
  } else {
    s.monitorCells.push({ court, time_slot: timeSlot });
  }
  renderChoices();
  preview();
}

// --- PLACEHOLDER_HELPERS ---

function allCourts() { return activeState().snapshot?.courts || []; }
function allTimes() { return activeState().snapshot?.times || []; }

function isSelectedCell(court, timeSlot) {
  return activeState().selectedCells.some((item) => sameCell(item, court, timeSlot));
}

function isMonitorCell(court, timeSlot) {
  return activeState().monitorCells.some((item) => sameCell(item, court, timeSlot));
}

function sameCell(item, court, timeSlot) {
  return String(item.court?.site_id) === String(court.site_id) && sameTime(item.time_slot, timeSlot);
}

function sameTime(a, b) {
  return a?.start_time === b?.start_time && a?.end_time === b?.end_time;
}

function selectionKey(court, timeSlot) {
  return `${court?.site_id || ""}|${timeSlot?.start_time || ""}|${timeSlot?.end_time || ""}`;
}

function normalizeTimes(slots) { return Array.from(slots || []); }

function slotStatus(court, timeSlot) {
  const s = activeState();
  if (!s.siteStatusQueried) return { available: true, label: "可选", desc: "普通抢票模式直接构造预约请求" };
  const item = (s.siteListSnapshot?.items || []).find((entry) => sameCell(entry, court, timeSlot));
  if (!item) return { available: true, label: "未知", desc: "当前没有这个场地时间的预约状态" };
  if (item.available) return { available: true, label: "可约", desc: "当前场地状态显示可预约" };
  const owner = String(item.member_name || "").trim();
  const reason = item.disabled_desc || item.disabled_reason || statusText(item.status) || "不可预约";
  const mobile = String(item.mobile || "").trim();
  return {
    available: false,
    reserved: !!owner,
    label: owner ? compactReason(reason, owner) : "不可预约",
    owner,
    desc: [owner, mobile, reason].filter(Boolean).join(" · ") || "当前场地状态显示不可预约",
  };
}

function compactReason(reason, owner) {
  let text = String(reason || "不可预约").trim();
  const name = String(owner || "").trim();
  if (name && text.startsWith(name)) {
    text = text.slice(name.length).trim();
  }
  return text || "不可预约";
}

function showNotice(message) {
  $("subtitle").textContent = message;
  setTimeout(renderSubtitle, 1600);
}

function updateMonitorControls() {
  const enabled = $("monitorEnabledInput").checked;
  $("monitorIntervalInput").disabled = !enabled;
}

function normalizeDates(values) {
  const seen = new Set();
  const dates = [];
  for (const value of values) {
    const date = normalizeDate(value);
    if (date && !seen.has(date)) { seen.add(date); dates.push(date); }
  }
  return dates;
}

function normalizeDate(value) {
  const text = String(value || "").trim().replaceAll("-", "/");
  if (!text) return "";
  const match = text.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})$/);
  if (!match) return text;
  return `${match[1]}/${match[2].padStart(2, "0")}/${match[3].padStart(2, "0")}`;
}

function toDateInputValue(value) {
  const date = normalizeDate(value);
  return date ? date.replaceAll("/", "-") : "";
}

function setDateField(id, value) {
  $(id).value = toDateInputValue(value);
}

function dateFieldValue(id) {
  return normalizeDate($(id).value);
}

function normalizeScheduledStart(value) { return String(value || "").trim().replace("T", " "); }

function toDatetimeLocalValue(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const normalized = text.replace("/", "-").replace("/", "-").replace(" ", "T");
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(normalized)) return normalized;
  return "";
}

function defaultScheduledStartValue() {
  const date = new Date(Date.now() + 60 * 1000);
  const pad = (v) => String(v).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function requestMode() { return $("pairModeInput").checked ? "pair" : "single"; }

function tabSummary(tab, index) {
  const s = tab.state || createTabState();
  const params = s.params || {};
  const dates = s.selectedDates?.length ? s.selectedDates : normalizeDates(params.dates || (params.date ? [params.date] : []));
  const selections = (params.monitor_enabled ? s.monitorCells : s.selectedCells) || params.selections || params.monitor_selections || [];
  const date = compactDate(dates[0]);
  const time = timeRangeSummary(selections);
  if (date && time) return `${date} ${time}`;
  if (date) return date;
  return `任务 ${index + 1}`;
}

function compactDate(value) {
  const date = normalizeDate(value);
  const match = date.match(/^\d{4}\/(\d{2})\/(\d{2})$/);
  if (!match) return date;
  return `${Number(match[1])}/${Number(match[2])}`;
}

function weekdayText(value) {
  const date = normalizeDate(value);
  const match = date.match(/^(\d{4})\/(\d{2})\/(\d{2})$/);
  if (!match) return "未选择";
  const parsed = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  if (Number.isNaN(parsed.getTime())) return "未选择";
  return ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][parsed.getDay()];
}

function timeRangeSummary(selections) {
  const slots = (selections || []).map((item) => item.time_slot).filter(Boolean);
  if (!slots.length) return "";
  const starts = slots.map((slot) => minutesOf(slot.start_time)).filter((value) => value >= 0);
  const ends = slots.map((slot) => minutesOf(slot.end_time)).filter((value) => value >= 0);
  if (!starts.length || !ends.length) return "";
  return `${formatHour(Math.min(...starts))}-${formatHour(Math.max(...ends))}`;
}

function minutesOf(value) {
  const match = String(value || "").match(/^(\d{1,2}):(\d{2})/);
  if (!match) return -1;
  return Number(match[1]) * 60 + Number(match[2]);
}

function formatHour(minutes) {
  const hour = Math.floor(minutes / 60);
  const minute = minutes % 60;
  return minute ? `${hour}:${String(minute).padStart(2, "0")}` : String(hour);
}

function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function statusText(status) {
  if (String(status) === "1") return "不可预约";
  if (String(status) === "0") return "未开放";
  return "";
}

// --- PLACEHOLDER_ACTIONS ---

async function preview() {
  const data = await api("/api/preview", { method: "POST", body: JSON.stringify(currentParams()) });
  $("requestView").textContent = JSON.stringify(data, null, 2);
  activeState().previewPinned = true;
}

async function save() {
  const data = await api("/api/save", { method: "POST", body: JSON.stringify(currentParams()) });
  renderStatus(data);
}

async function start() {
  const s = activeState();
  if ($("monitorEnabledInput").checked && !s.monitorCells.length) {
    showNotice("监听下单需要先查询并选择不可预约场地时间");
    return;
  }
  s.previewPinned = false;
  const data = await api("/api/start", { method: "POST", body: JSON.stringify(currentParams()) });
  renderStatus(data);
  renderTabs();
}

async function stop() {
  activeState().previewPinned = false;
  const data = await api("/api/stop", { method: "POST", body: "{}" });
  renderStatus(data);
  renderTabs();
}

async function clearLogs() {
  const data = await api("/api/clear-logs", { method: "POST", body: "{}" });
  renderStatus(data);
}

async function querySiteStatus() {
  const s = activeState();
  const date = s.selectedDates[0] || dateFieldValue("dateInput") || "";
  if (!date) { showNotice("请先选择顶部日期"); return; }
  s.previewPinned = false;
  const data = await api("/api/site-status", { method: "POST", body: JSON.stringify(currentParams()) });
  if (!data.success) {
    if (data.request) $("requestView").textContent = JSON.stringify(data.request, null, 2);
    return;
  }
  s.siteListSnapshot = data.snapshot;
  s.siteStatusQueried = true;
  const occupiedKeys = new Set(
    (s.siteListSnapshot.items || []).filter((item) => !item.available).map((item) => selectionKey(item.court, item.time_slot))
  );
  s.monitorCells = s.monitorCells.filter((item) => occupiedKeys.has(selectionKey(item.court, item.time_slot)));
  if (data.request) $("requestView").textContent = JSON.stringify(data.request, null, 2);
  renderChoices();
  await preview();
}

function autoQuerySiteStatus() {
  querySiteStatus().catch(() => {});
}

// --- PLACEHOLDER_RESERVED ---

function reservedItems(snapshot) {
  return (snapshot?.items || [])
    .filter((item) => !item.available)
    .sort((a, b) => {
      const byCourt = courtOrder(a.court) - courtOrder(b.court);
      if (byCourt) return byCourt;
      return String(a.time_slot?.start_time || "").localeCompare(String(b.time_slot?.start_time || ""));
    });
}

function reservedPeopleSummary(snapshot) {
  const seen = new Set();
  const names = [];
  for (const item of reservedItems(snapshot)) {
    const name = String(item.member_name || "").trim();
    if (!name || seen.has(name)) continue;
    seen.add(name);
    names.push(name);
  }
  return names.length ? `已预约：${names.join("、")}` : "";
}

function courtOrder(court) {
  const index = allCourts().findIndex((item) => String(item.site_id) === String(court?.site_id));
  return index >= 0 ? index : Number.MAX_SAFE_INTEGER;
}

// --- PLACEHOLDER_STATUS ---

function renderStatus(status) {
  const s = activeState();
  s.running = status.running;
  s.waitingForSchedule = status.waiting_for_schedule;
  $("runState").textContent = status.waiting_for_schedule ? "等待定时" : status.running ? "运行中" : "已停止";
  $("runState").style.background = status.waiting_for_schedule ? "#fef9c3" : status.running ? "#dcfce7" : "#eef4f3";
  s.logs = status.logs || [];
  renderLogs();
  if (status.last_request && (!s.previewPinned || status.running || status.waiting_for_schedule)) {
    $("requestView").textContent = JSON.stringify(status.last_request, null, 2);
  }
  updateAddTabButton();
  postNativeStatus(status);
}

function postNativeStatus(status) {
  try {
    window.webkit?.messageHandlers?.nativeBridge?.postMessage({
      event: "status",
      running: status.running === true,
      waiting_for_schedule: status.waiting_for_schedule === true,
    });
  } catch {}
}

function renderLogs() {
  const s = activeState();
  const successContainer = $("successLogView");
  const logContainer = $("logView");
  const visibleLogs = s.logs
    .map((line, index) => ({ line, index, successful: isSuccessLog(line) }));
  const grabbedLogs = visibleLogs
    .map((item) => ({ ...item, summary: grabbedSummary(item.line) }))
    .filter((item) => item.summary)
    .slice(-6)
    .reverse();

  successContainer.innerHTML = "";
  logContainer.innerHTML = "";
  if (!visibleLogs.length) {
    const empty = document.createElement("div");
    empty.className = "log-empty";
    empty.textContent = "暂无日志";
    logContainer.appendChild(empty);
    return;
  }

  if (grabbedLogs.length) {
    const title = document.createElement("div");
    title.className = "success-title";
    title.textContent = "已抢到";
    successContainer.appendChild(title);
  }

  for (const item of grabbedLogs) {
    const row = document.createElement("div");
    row.className = "success-log-card";
    row.textContent = item.summary;
    successContainer.appendChild(row);
  }

  for (const item of visibleLogs) {
    const row = document.createElement("div");
    row.className = `log-line${item.successful ? " success" : ""}`;
    row.textContent = item.line;
    logContainer.appendChild(row);
  }
  scrollLogsToBottom();
}

function scrollLogsToBottom() {
  if (!$("autoScrollInput").checked) return;
  requestAnimationFrame(() => { $("logView").scrollTop = $("logView").scrollHeight; });
}

function isSuccessLog(line) { return line.includes("成功") && !line.includes("失败"); }

function grabbedSummary(line) {
  const swiftTarget = line.match(/请求#\d+ 成功(?:\([^)]*\))?[:：]\s*(.+)$/);
  if (swiftTarget) return swiftTarget[1];
  const completedTarget = line.match(/完成第 \d+ 个请求（成功）：(.+)$/);
  if (completedTarget) return completedTarget[1];
  const target = line.match(/请求（成功）：(.+)$/);
  if (target) return target[1];
  const monitorTarget = line.match(/监听下单第 \d+ 个请求（成功）：(.+)$/);
  if (monitorTarget) return monitorTarget[1];
  return "";
}

function toggleRequestPanel() {
  const s = activeState();
  s.requestCollapsed = !s.requestCollapsed;
  renderRequestPanel();
}

function renderRequestPanel() {
  const s = activeState();
  const panel = document.querySelector(".request-panel");
  const layout = document.querySelector(".layout");
  panel.classList.toggle("collapsed", s.requestCollapsed);
  layout.classList.toggle("request-collapsed", s.requestCollapsed);
  $("toggleRequestBtn").textContent = s.requestCollapsed ? "展开" : "收起";
  $("toggleRequestBtn").setAttribute("aria-expanded", String(!s.requestCollapsed));
}

// --- PLACEHOLDER_RESIZE ---

function setupLogResize() {
  const handle = $("logResizeHandle");
  const layout = document.querySelector(".layout");
  const logPanel = document.querySelector(".log-panel");
  let startY = 0;
  let startRequestHeight = 0;
  let startLogHeight = 0;

  handle.addEventListener("pointerdown", (event) => {
    const requestPanel = document.querySelector(".request-panel");
    if (activeState().requestCollapsed) {
      activeState().requestCollapsed = false;
      renderRequestPanel();
    }
    startY = event.clientY;
    startRequestHeight = requestPanel.getBoundingClientRect().height;
    startLogHeight = logPanel.getBoundingClientRect().height;
    handle.setPointerCapture(event.pointerId);
    logPanel.classList.add("resizing");
  });

  handle.addEventListener("pointermove", (event) => {
    if (!logPanel.classList.contains("resizing")) return;
    const delta = event.clientY - startY;
    const available = startRequestHeight + startLogHeight;
    const requestHeight = Math.max(140, Math.min(available - 220, startRequestHeight + delta));
    const logHeight = Math.max(220, available - requestHeight);
    layout.style.setProperty("--request-row", `${requestHeight}px`);
    layout.style.setProperty("--log-row", `${logHeight}px`);
  });

  const finish = (event) => {
    if (!logPanel.classList.contains("resizing")) return;
    logPanel.classList.remove("resizing");
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
  };
  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", finish);
}

// --- PLACEHOLDER_BOOT ---

async function bootTab(tab) {
  try {
    const metadata = await apiForTab(tab, "/api/metadata");
    tab.state.snapshot = metadata.snapshot;
    tab.state.siteListSnapshot = null;
    applyParams(metadata.params);
    if (!$("scheduledStartInput").value) {
      $("scheduledStartInput").value = defaultScheduledStartValue();
    }
    const defDate = defaultDate();
    if (!activeState().selectedDates.length) {
      activeState().selectedDates = [defDate];
      setDateField("dateInput", defDate);
    }
    renderChoices();
    await preview();
    await refreshStatus();
    autoQuerySiteStatus();
    startPolling(tab);
  } catch (error) {
    $("subtitle").textContent = error.message;
  }
}

function startPolling(tab) {
  stopPolling(tab);
  tab.pollTimer = setInterval(async () => {
    if (tab.id !== activeTabId) {
      try {
        const status = await apiForTab(tab, "/api/status");
        tab.state.running = status.running;
        tab.state.waitingForSchedule = status.waiting_for_schedule;
        tab.state.logs = status.logs || [];
        renderTabs();
      } catch {}
    } else {
      await refreshStatus();
    }
  }, 1200);
}

function stopPolling(tab) {
  if (tab.pollTimer) {
    clearInterval(tab.pollTimer);
    tab.pollTimer = null;
  }
}

async function refreshStatus() {
  const status = await api("/api/status");
  renderStatus(status);
  renderTabs();
}

async function boot() {
  const saved = loadTabs();
  if (saved && saved.tabs && saved.tabs.length) {
    for (const entry of saved.tabs) {
      const tab = { id: entry.id, clientId: entry.clientId, state: createTabState(), pollTimer: null };
      tabs.push(tab);
    }
    activeTabId = saved.activeTabId || tabs[0].id;
  } else {
    const clientId = getOrCreateClientId();
    const tab = { id: generateId(), clientId, state: createTabState(), pollTimer: null };
    tabs.push(tab);
    activeTabId = tab.id;
    saveTabs();
  }
  renderTabs();
  const tab = activeTab();
  await bootTab(tab);
  for (const t of tabs) {
    if (t.id !== activeTabId) startPolling(t);
  }
}

function getOrCreateClientId() {
  const CLIENT_ID_CACHE_KEY = "badminton_booker.client_id";
  try {
    const cached = window.localStorage?.getItem(CLIENT_ID_CACHE_KEY);
    if (cached) return cached;
    const id = generateId();
    window.localStorage?.setItem(CLIENT_ID_CACHE_KEY, id);
    return id;
  } catch {
    return "default";
  }
}

// --- Templates ---

function defaultDate() {
  const d = new Date();
  d.setDate(d.getDate() + 7);
  const pad = (v) => String(v).padStart(2, "0");
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`;
}

function nextFridayDate() {
  const d = new Date();
  d.setDate(d.getDate() + 7);
  const day = d.getDay();
  const daysUntilFriday = ((5 - day + 7) % 7);
  d.setDate(d.getDate() + daysUntilFriday);
  const pad = (v) => String(v).padStart(2, "0");
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`;
}

function applyTemplate(date, timeStartHours, mode, btnId) {
  for (const id of ["tplMorningBtn", "tplFriEveBtn", "tplFriEve2Btn"]) {
    $(id).classList.toggle("active", id === btnId);
  }

  const s = activeState();
  s.selectedDates = [date];
  setDateField("dateInput", date);

  const courts = allCourts().slice(0, 7);
  const times = allTimes().filter((t) => timeStartHours.includes(t.start_time));
  const cells = [];
  for (const court of courts) {
    for (const timeSlot of times) {
      cells.push({ court, time_slot: timeSlot });
    }
  }
  s.selectedCells = cells;

  $("pairModeInput").checked = mode === "pair";

  renderChoices();
  preview();
}

// --- Event listeners ---

$("previewBtn").addEventListener("click", preview);
$("saveBtn").addEventListener("click", save);
$("startBtn").addEventListener("click", start);
$("stopBtn").addEventListener("click", stop);
$("tplMorningBtn").addEventListener("click", () => applyTemplate(defaultDate(), ["09:00"], "single", "tplMorningBtn"));
$("tplFriEveBtn").addEventListener("click", () => applyTemplate(nextFridayDate(), ["20:00", "21:00"], "pair", "tplFriEveBtn"));
$("tplFriEve2Btn").addEventListener("click", () => applyTemplate(nextFridayDate(), ["19:00"], "single", "tplFriEve2Btn"));
$("addTabBtn").addEventListener("click", handleAddTab);
$("pairModeInput").addEventListener("change", preview);
$("monitorEnabledInput").addEventListener("change", () => {
  const s = activeState();
  if (!$("monitorEnabledInput").checked) {
    s.monitorCells = [];
  }
  renderChoices();
  preview();
});
$("toggleRequestBtn").addEventListener("click", toggleRequestPanel);
$("clearLogsBtn").addEventListener("click", clearLogs);
$("autoScrollInput").addEventListener("change", scrollLogsToBottom);

for (const id of ["intervalInput", "maxAttemptsInput", "dryRunInput", "scheduledStartInput", "monitorIntervalInput", "wxTokenInput", "shopIdInput", "brandCodeInput"]) {
  $(id).addEventListener("change", preview);
}
$("scheduleEnabledInput").addEventListener("change", () => {
  if ($("scheduleEnabledInput").checked) {
    const now = new Date();
    const pad = (v) => String(v).padStart(2, "0");
    $("scheduledStartInput").value = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T23:59:30`;
  }
  preview();
});
$("wxTokenInput").addEventListener("input", cacheWxToken);
$("dateInput").addEventListener("change", () => {
  const s = activeState();
  const date = dateFieldValue("dateInput");
  if (date) {
    s.selectedDates = [date];
    setDateField("dateInput", s.selectedDates[0]);
    s.siteStatusQueried = false;
    s.siteListSnapshot = null;
    s.monitorCells = [];
  }
  renderChoices();
  preview();
  autoQuerySiteStatus();
});

boot().catch((error) => { $("subtitle").textContent = error.message; });
setupLogResize();
setupEditableFields();
renderRequestPanel();

window.bmintonNativeCommands = {
  start: () => start().catch((error) => showNotice(`启动失败：${error.message}`)),
  stop: () => stop().catch((error) => showNotice(`停止失败：${error.message}`)),
  nextTab: () => switchRelativeTab(1),
  previousTab: () => switchRelativeTab(-1),
};
