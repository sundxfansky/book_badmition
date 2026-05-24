const state = {
  snapshot: null,
  params: null,
  selectedDates: [],
  selectedCells: [],
  logs: [],
  requestCollapsed: false,
};

const WX_TOKEN_CACHE_KEY = "badminton_booker.wx_token";
const CLIENT_ID_CACHE_KEY = "badminton_booker.client_id";

const $ = (id) => document.getElementById(id);
const clientId = getClientId();

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "content-type": "application/json",
      "x-client-id": clientId,
      ...(options.headers || {}),
    },
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function currentParams() {
  cacheWxToken();
  return {
    dry_run: $("dryRunInput").checked,
    verify_ssl: $("verifySslInput").checked,
    interval_seconds: Number($("intervalInput").value || 0.1),
    max_attempts: Number($("maxAttemptsInput").value || 100000),
    schedule_enabled: $("scheduleEnabledInput").checked,
    scheduled_start_at: normalizeScheduledStart($("scheduledStartInput").value),
    date: state.selectedDates[0] || $("dateInput").value.trim(),
    dates: state.selectedDates,
    request_mode: requestMode(),
    selections: state.selectedCells,
    headers: {
      "wx-token": $("wxTokenInput").value.trim(),
      "shop-id": $("shopIdInput").value.trim(),
      "brand-code": $("brandCodeInput").value.trim(),
    },
  };
}

function applyParams(params, options = {}) {
  state.params = params;
  const cachedToken = getCachedWxToken();
  const importedToken = params.headers?.["wx-token"] || "";
  const token = options.preferParamsToken ? importedToken || cachedToken : cachedToken || importedToken;
  $("dateInput").value = params.date || "";
  $("intervalInput").value = params.interval_seconds ?? 0.1;
  $("maxAttemptsInput").value = params.max_attempts ?? 100000;
  $("dryRunInput").checked = params.dry_run === true;
  $("verifySslInput").checked = params.verify_ssl === true;
  $("scheduleEnabledInput").checked = params.schedule_enabled === true;
  $("scheduledStartInput").value = toDatetimeLocalValue(params.scheduled_start_at || "");
  $("wxTokenInput").value = token;
  $("shopIdInput").value = params.headers?.["shop-id"] || "";
  $("brandCodeInput").value = params.headers?.["brand-code"] || "";
  $("pairModeInput").checked = params.request_mode === "pair";
  $("singleModeInput").checked = params.request_mode !== "pair";

  state.selectedDates = normalizeDates(params.dates || (params.date ? [params.date] : []));
  state.selectedCells = params.selections || defaultSelections(params);
}

function cacheWxToken() {
  const token = $("wxTokenInput").value.trim();
  if (token) {
    setCachedWxToken(token);
  } else {
    clearCachedWxToken();
  }
}

function getCachedWxToken() {
  try {
    return window.localStorage?.getItem(WX_TOKEN_CACHE_KEY) || "";
  } catch {
    return "";
  }
}

function setCachedWxToken(token) {
  try {
    window.localStorage?.setItem(WX_TOKEN_CACHE_KEY, token);
  } catch {
    return;
  }
}

function clearCachedWxToken() {
  try {
    window.localStorage?.removeItem(WX_TOKEN_CACHE_KEY);
  } catch {
    return;
  }
}

function defaultSelections(params) {
  const courts = params.courts || params.fixed_courts || state.snapshot?.fixed_courts || [];
  const times = params.time_slots || params.selected_times || state.snapshot?.selected_times || [];
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
  renderDates();
  renderScheduleGrid();
}

function renderSubtitle() {
  if (!state.snapshot) return;
  $("subtitle").textContent = `${state.selectedDates.length} 个日期 · 已选 ${state.selectedCells.length} 个场地时间`;
}

function renderDates() {
  const dateList = $("dateList");
  dateList.innerHTML = "";
  for (const date of state.selectedDates) {
    const chip = document.createElement("div");
    chip.className = "choice active date-chip";
    chip.innerHTML = `<span>${date}</span><button type="button" aria-label="删除 ${date}">x</button>`;
    chip.querySelector("button").addEventListener("click", () => {
      state.selectedDates = state.selectedDates.filter((item) => item !== date);
      $("dateInput").value = state.selectedDates[0] || "";
      renderChoices();
      preview();
    });
    dateList.appendChild(chip);
  }
}

function renderScheduleGrid() {
  const grid = $("scheduleGrid");
  const courts = allCourts();
  const times = allTimes();
  grid.style.gridTemplateColumns = `112px repeat(${courts.length}, minmax(86px, 1fr))`;
  grid.innerHTML = "";

  grid.appendChild(cell("时间 / 场地", "schedule-head schedule-corner"));
  for (const court of courts) {
    grid.appendChild(cell(court.site_name, "schedule-head"));
  }

  for (const timeSlot of times) {
    grid.appendChild(cell(`${timeSlot.start_time}-${timeSlot.end_time}`, "schedule-time"));
    for (const court of courts) {
      const button = document.createElement("button");
      button.className = `schedule-cell${isSelectedCell(court, timeSlot) ? " active" : ""}`;
      button.innerHTML = `<span>${timeSlot.price} 元</span>`;
      button.addEventListener("click", () => toggleCell(court, timeSlot));
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
  if (isSelectedCell(court, timeSlot)) {
    state.selectedCells = state.selectedCells.filter((item) => !sameCell(item, court, timeSlot));
  } else {
    state.selectedCells.push({ court, time_slot: timeSlot });
  }
  renderChoices();
  preview();
}

function allCourts() {
  return state.snapshot?.courts || [];
}

function allTimes() {
  return state.snapshot?.times || [];
}

function isSelectedCell(court, timeSlot) {
  return state.selectedCells.some((item) => sameCell(item, court, timeSlot));
}

function sameCell(item, court, timeSlot) {
  return String(item.court?.site_id) === String(court.site_id) && sameTime(item.time_slot, timeSlot);
}

function sameTime(a, b) {
  return a?.start_time === b?.start_time && a?.end_time === b?.end_time;
}

function normalizeTimes(slots) {
  return Array.from(slots || []);
}

function showNotice(message) {
  $("subtitle").textContent = message;
  setTimeout(renderSubtitle, 1600);
}

function normalizeDates(values) {
  const seen = new Set();
  const dates = [];
  for (const value of values) {
    const date = normalizeDate(value);
    if (date && !seen.has(date)) {
      seen.add(date);
      dates.push(date);
    }
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

function normalizeScheduledStart(value) {
  return String(value || "").trim().replace("T", " ");
}

function toDatetimeLocalValue(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const normalized = text.replace("/", "-").replace("/", "-").replace(" ", "T");
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(normalized)) {
    return normalized;
  }
  return "";
}

function defaultScheduledStartValue() {
  const date = new Date(Date.now() + 60 * 1000);
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function requestMode() {
  return $("pairModeInput").checked ? "pair" : "single";
}

async function preview() {
  const data = await api("/api/preview", {
    method: "POST",
    body: JSON.stringify(currentParams()),
  });
  $("requestView").textContent = JSON.stringify(data, null, 2);
}

async function save() {
  const data = await api("/api/save", {
    method: "POST",
    body: JSON.stringify(currentParams()),
  });
  renderStatus(data);
}

async function start() {
  const data = await api("/api/start", {
    method: "POST",
    body: JSON.stringify(currentParams()),
  });
  renderStatus(data);
}

async function stop() {
  const data = await api("/api/stop", { method: "POST", body: "{}" });
  renderStatus(data);
}

async function clearLogs() {
  const data = await api("/api/clear-logs", { method: "POST", body: "{}" });
  renderStatus(data);
}

async function exportParams() {
  const params = currentParams();
  delete params.headers["wx-token"];
  $("jsonBox").value = JSON.stringify(params, null, 2);
}

async function importParams() {
  const params = JSON.parse($("jsonBox").value);
  applyParams(params, { preferParamsToken: true });
  renderChoices();
  await save();
  await preview();
}

function renderStatus(status) {
  $("runState").textContent = status.waiting_for_schedule ? "等待定时" : status.running ? "运行中" : "已停止";
  $("runState").style.background = status.waiting_for_schedule ? "#fef9c3" : status.running ? "#dcfce7" : "#eef4f3";
  state.logs = status.logs || [];
  renderLogs();
  if (status.last_request) {
    $("requestView").textContent = JSON.stringify(status.last_request, null, 2);
  }
}

function renderLogs() {
  const successContainer = $("successLogView");
  const logContainer = $("logView");
  const query = $("logSearchInput").value.trim().toLowerCase();
  const filter = $("logFilterInput").value;
  const visibleLogs = state.logs
    .filter((line) => !query || line.toLowerCase().includes(query))
    .filter((line) => matchesLogFilter(line, filter))
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
    empty.textContent = query ? "没有匹配的日志" : "暂无日志";
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
  requestAnimationFrame(() => {
    $("logView").scrollTop = $("logView").scrollHeight;
  });
}

function isSuccessLog(line) {
  return line.includes("成功") && !line.includes("失败");
}

function isFailureLog(line) {
  return /失败|HTTP 错误|网络错误|错误|CERTIFICATE|HTTP \d{3}/i.test(line) && !isSuccessLog(line);
}

function matchesLogFilter(line, filter) {
  if (filter === "success") return isSuccessLog(line);
  if (filter === "failure") return isFailureLog(line);
  if (filter === "request") return /请求|提交|准备|完成/.test(line);
  if (filter === "network") return /网络|HTTP|SSL|证书|CERTIFICATE/i.test(line);
  return true;
}

function grabbedSummary(line) {
  const target = line.match(/请求（成功）：(.+)$/);
  if (target) return target[1];
  return "";
}

function updateLogFontSize() {
  const value = $("logFontInput").value;
  $("logFontValue").textContent = value;
  $("logView").style.fontSize = `${value}px`;
  $("successLogView").style.fontSize = `${value}px`;
}

function toggleRequestPanel() {
  state.requestCollapsed = !state.requestCollapsed;
  renderRequestPanel();
}

function renderRequestPanel() {
  const panel = document.querySelector(".request-panel");
  const layout = document.querySelector(".layout");
  panel.classList.toggle("collapsed", state.requestCollapsed);
  layout.classList.toggle("request-collapsed", state.requestCollapsed);
  $("toggleRequestBtn").textContent = state.requestCollapsed ? "展开" : "收起";
  $("toggleRequestBtn").setAttribute("aria-expanded", String(!state.requestCollapsed));
}

function setupLogResize() {
  const handle = $("logResizeHandle");
  const layout = document.querySelector(".layout");
  const logPanel = document.querySelector(".log-panel");
  let startY = 0;
  let startRequestHeight = 0;
  let startLogHeight = 0;

  handle.addEventListener("pointerdown", (event) => {
    const requestPanel = document.querySelector(".request-panel");
    if (state.requestCollapsed) {
      state.requestCollapsed = false;
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
    if (handle.hasPointerCapture(event.pointerId)) {
      handle.releasePointerCapture(event.pointerId);
    }
  };
  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", finish);
}

async function refreshStatus() {
  const status = await api("/api/status");
  renderStatus(status);
}

async function boot() {
  const metadata = await api("/api/metadata");
  state.snapshot = metadata.snapshot;
  applyParams(metadata.params);
  if (!$("scheduledStartInput").value) {
    $("scheduledStartInput").value = defaultScheduledStartValue();
  }
  $("newDateInput").value = state.snapshot.date;
  renderChoices();
  await preview();
  await refreshStatus();
  setInterval(refreshStatus, 1200);
}

$("previewBtn").addEventListener("click", preview);
$("saveBtn").addEventListener("click", save);
$("startBtn").addEventListener("click", start);
$("stopBtn").addEventListener("click", stop);
$("exportBtn").addEventListener("click", exportParams);
$("importBtn").addEventListener("click", importParams);
$("singleModeInput").addEventListener("change", preview);
$("pairModeInput").addEventListener("change", preview);
$("toggleRequestBtn").addEventListener("click", toggleRequestPanel);
$("clearLogsBtn").addEventListener("click", clearLogs);
$("logSearchInput").addEventListener("input", renderLogs);
$("logFilterInput").addEventListener("change", renderLogs);
$("logFontInput").addEventListener("input", updateLogFontSize);
$("autoScrollInput").addEventListener("change", scrollLogsToBottom);
$("addDateBtn").addEventListener("click", () => {
  const date = normalizeDate($("newDateInput").value);
  if (!date) return;
  state.selectedDates = normalizeDates([...state.selectedDates, date]);
  $("dateInput").value = state.selectedDates[0] || "";
  renderChoices();
  preview();
});

for (const id of ["intervalInput", "maxAttemptsInput", "dryRunInput", "verifySslInput", "scheduleEnabledInput", "scheduledStartInput", "wxTokenInput", "shopIdInput", "brandCodeInput"]) {
  $(id).addEventListener("change", preview);
}
$("wxTokenInput").addEventListener("input", cacheWxToken);

$("dateInput").addEventListener("change", () => {
  const date = normalizeDate($("dateInput").value);
  if (date) {
    state.selectedDates = normalizeDates([date, ...state.selectedDates]);
    $("dateInput").value = state.selectedDates[0] || "";
  }
  renderChoices();
  preview();
});

boot().catch((error) => {
  $("subtitle").textContent = error.message;
});

updateLogFontSize();
setupLogResize();
renderRequestPanel();

function getClientId() {
  try {
    const cached = window.localStorage?.getItem(CLIENT_ID_CACHE_KEY);
    if (cached) return cached;
    const id = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    window.localStorage?.setItem(CLIENT_ID_CACHE_KEY, id);
    return id;
  } catch {
    return "default";
  }
}
