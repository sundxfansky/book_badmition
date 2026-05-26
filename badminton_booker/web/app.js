const state = {
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
    monitor_enabled: $("monitorEnabledInput").checked,
    monitor_date: normalizeDate($("monitorDateInput").value) || state.selectedDates[0] || $("dateInput").value.trim(),
    monitor_interval_seconds: Number($("monitorIntervalInput").value || 20),
    monitor_selections: state.monitorCells,
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
  $("monitorEnabledInput").checked = params.monitor_enabled === true;
  $("monitorDateInput").value = params.monitor_date || params.date || "";
  $("monitorIntervalInput").value = params.monitor_interval_seconds ?? 20;
  $("reservedDateInput").value = params.monitor_date || params.date || "";
  $("wxTokenInput").value = token;
  $("shopIdInput").value = params.headers?.["shop-id"] || "";
  $("brandCodeInput").value = params.headers?.["brand-code"] || "";
  $("pairModeInput").checked = params.request_mode === "pair";
  $("singleModeInput").checked = params.request_mode !== "pair";

  state.selectedDates = normalizeDates(params.dates || (params.date ? [params.date] : []));
  state.selectedCells = params.selections || defaultSelections(params);
  state.monitorCells = params.monitor_selections || [];
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
  const mode = $("monitorEnabledInput").checked
    ? `监听下单 · 已选 ${state.monitorCells.length} 个已约场地时间`
    : `普通抢票 · 已选 ${state.selectedCells.length} 个场地时间`;
  $("subtitle").textContent = `${state.selectedDates.length} 个日期 · ${mode}`;
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
      if ($("monitorDateInput").value === date) {
        $("monitorDateInput").value = state.selectedDates[0] || "";
        state.siteStatusQueried = false;
        state.siteListSnapshot = null;
        state.monitorCells = [];
      }
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
  const monitorMode = $("monitorEnabledInput").checked;
  updateMonitorControls();
  grid.style.gridTemplateColumns = `92px repeat(${courts.length}, minmax(70px, 1fr))`;
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
      const disabled = monitorMode && (!state.siteStatusQueried || status.available);
      button.className = `schedule-cell${selected ? " active" : ""}${status.available ? " available" : " occupied"}${disabled ? " disabled" : ""}`;
      button.disabled = monitorMode && !state.siteStatusQueried;
      const mainText = status.owner ? status.owner : `${timeSlot.price} 元`;
      const mainClass = status.owner ? "slot-owner" : "";
      button.innerHTML = `<span class="${mainClass}">${escapeHtml(mainText)}</span><small>${escapeHtml(status.label)}</small>`;
      button.title = status.desc;
      button.addEventListener("click", () => {
        if (monitorMode) {
          if (!state.siteStatusQueried) {
            showNotice("请先点击查询当前场地预约情况");
            return;
          }
          if (status.available) {
            showNotice("这个场地当前可预约，请切换到普通抢票模式");
            return;
          }
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
  if (isSelectedCell(court, timeSlot)) {
    state.selectedCells = state.selectedCells.filter((item) => !sameCell(item, court, timeSlot));
  } else {
    state.selectedCells.push({ court, time_slot: timeSlot });
  }
  renderChoices();
  preview();
}

function toggleMonitorCell(court, timeSlot) {
  if (isMonitorCell(court, timeSlot)) {
    state.monitorCells = state.monitorCells.filter((item) => !sameCell(item, court, timeSlot));
  } else {
    state.monitorCells.push({ court, time_slot: timeSlot });
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

function isMonitorCell(court, timeSlot) {
  return state.monitorCells.some((item) => sameCell(item, court, timeSlot));
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

function normalizeTimes(slots) {
  return Array.from(slots || []);
}

function slotStatus(court, timeSlot) {
  const monitorMode = $("monitorEnabledInput").checked;
  if (!monitorMode) {
    return { available: true, label: "可选", desc: "普通抢票模式直接构造预约请求" };
  }
  if (!state.siteStatusQueried) {
    return { available: true, label: "未查询", desc: "开启监听后，请先查询当前场地预约情况" };
  }
  const item = (state.siteListSnapshot?.items || []).find((entry) => sameCell(entry, court, timeSlot));
  if (!item) {
    return { available: true, label: "未知", desc: "当前没有这个场地时间的预约状态" };
  }
  if (item.available) {
    return { available: true, label: "可约", desc: "当前场地状态显示可预约" };
  }
  const owner = String(item.member_name || "").trim();
  const reason = item.disabled_desc || item.disabled_reason || statusText(item.status) || "已约";
  const mobile = String(item.mobile || "").trim();
  return {
    available: false,
    label: owner ? reason : "已约",
    owner,
    desc: [owner, mobile, reason].filter(Boolean).join(" · ") || "当前场地状态显示不可预约",
  };
}

function showNotice(message) {
  $("subtitle").textContent = message;
  setTimeout(renderSubtitle, 1600);
}

function updateMonitorControls() {
  const enabled = $("monitorEnabledInput").checked;
  $("monitorDateInput").disabled = !enabled;
  $("monitorIntervalInput").disabled = !enabled;
  $("querySiteStatusBtn").disabled = !enabled;
  if (!enabled) {
    $("siteStatusSummary").textContent = "开启监听后，先选择日期再查询。";
  } else if (!state.siteStatusQueried) {
    $("siteStatusSummary").textContent = "请选择日期，然后点击查询当前场地预约情况。";
  }
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
  state.previewPinned = true;
}

async function save() {
  const data = await api("/api/save", {
    method: "POST",
    body: JSON.stringify(currentParams()),
  });
  renderStatus(data);
}

async function start() {
  if ($("monitorEnabledInput").checked && !state.monitorCells.length) {
    showNotice("监听下单需要先查询并选择已约场地时间");
    return;
  }
  state.previewPinned = false;
  const data = await api("/api/start", {
    method: "POST",
    body: JSON.stringify(currentParams()),
  });
  renderStatus(data);
}

async function stop() {
  state.previewPinned = false;
  const data = await api("/api/stop", { method: "POST", body: "{}" });
  renderStatus(data);
}

async function clearLogs() {
  const data = await api("/api/clear-logs", { method: "POST", body: "{}" });
  renderStatus(data);
}

async function querySiteStatus() {
  if (!$("monitorEnabledInput").checked) {
    showNotice("请先开启监听场地下单开关");
    return;
  }
  const date = normalizeDate($("monitorDateInput").value) || state.selectedDates[0] || "";
  if (!date) {
    showNotice("请先选择监听日期");
    return;
  }
  $("monitorDateInput").value = date;
  state.previewPinned = false;
  $("siteStatusSummary").textContent = `正在查询 ${date} 的场地预约情况...`;
  const data = await api("/api/site-status", {
    method: "POST",
    body: JSON.stringify(currentParams()),
  });
  if (!data.success) {
    $("siteStatusSummary").textContent = `查询失败：${data.error || data.message || "未知错误"}`;
    if (data.request) {
      $("requestView").textContent = JSON.stringify(data.request, null, 2);
    }
    return;
  }
  state.siteListSnapshot = data.snapshot;
  state.siteStatusQueried = true;
  const occupiedKeys = new Set(
    (state.siteListSnapshot.items || [])
      .filter((item) => !item.available)
      .map((item) => selectionKey(item.court, item.time_slot))
  );
  state.monitorCells = state.monitorCells.filter((item) => occupiedKeys.has(selectionKey(item.court, item.time_slot)));
  const people = reservedPeopleSummary(data.snapshot);
  $("siteStatusSummary").textContent = `${data.snapshot.date}：可约 ${data.available_count} 个，已约 ${data.occupied_count} 个${people ? ` · ${people}` : ""}`;
  if (data.request) {
    $("requestView").textContent = JSON.stringify(data.request, null, 2);
  }
  renderChoices();
  await preview();
}

async function queryReservedStatus() {
  const date = normalizeDate($("reservedDateInput").value) || state.selectedDates[0] || "";
  if (!date) {
    showNotice("请先输入要查询的日期");
    return;
  }
  $("reservedDateInput").value = date;
  $("reservedLookupSummary").textContent = `正在查询 ${date}...`;
  state.previewPinned = false;
  const params = { ...currentParams(), monitor_date: date };
  const data = await api("/api/site-status", {
    method: "POST",
    body: JSON.stringify(params),
  });
  if (!data.success) {
    $("reservedLookupSummary").textContent = `查询失败：${data.error || data.message || "未知错误"}`;
    renderReservedTable([]);
    if (data.request) {
      $("requestView").textContent = JSON.stringify(data.request, null, 2);
    }
    return;
  }
  state.reservedSnapshot = data.snapshot;
  const reservedRows = reservedItems(data.snapshot);
  $("reservedLookupSummary").textContent = `${data.snapshot.date} 已预约 ${reservedRows.length} 个`;
  if (data.request) {
    $("requestView").textContent = JSON.stringify(data.request, null, 2);
  }
  renderReservedTable(reservedRows);
}

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
  if (!names.length) return "";
  return `已预约：${names.join("、")}`;
}

function courtOrder(court) {
  const index = allCourts().findIndex((item) => String(item.site_id) === String(court?.site_id));
  return index >= 0 ? index : Number.MAX_SAFE_INTEGER;
}

function renderReservedTable(items) {
  const tbody = $("reservedTableBody");
  tbody.innerHTML = "";
  if (!items.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="6" class="empty-table">没有查询到被预约的场地。</td>';
    tbody.appendChild(row);
    return;
  }
  for (const item of items) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${escapeHtml(state.reservedSnapshot?.date || "")}</td>
      <td>${escapeHtml(item.court?.site_name || "-")}</td>
      <td>${escapeHtml(item.time_slot?.start_time || "?")}-${escapeHtml(item.time_slot?.end_time || "?")}</td>
      <td>${escapeHtml(reservedReason(item))}</td>
      <td>${escapeHtml(item.member_name || "-")}</td>
      <td>${escapeHtml(item.mobile || "-")}</td>
    `;
    tbody.appendChild(row);
  }
}

function reservedReason(item) {
  return item.disabled_desc || item.disabled_reason || statusText(item.status) || "已预约";
}

function statusText(status) {
  if (String(status) === "1") return "不可预约";
  if (String(status) === "0") return "未开放";
  return "";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function exportParams() {
  const params = currentParams();
  delete params.headers["wx-token"];
  $("jsonBox").value = JSON.stringify(params, null, 2);
}

async function importParams() {
  const params = JSON.parse($("jsonBox").value);
  applyParams(params, { preferParamsToken: true });
  state.siteStatusQueried = false;
  renderChoices();
  await save();
  await preview();
}

function renderStatus(status) {
  $("runState").textContent = status.waiting_for_schedule ? "等待定时" : status.running ? "运行中" : "已停止";
  $("runState").style.background = status.waiting_for_schedule ? "#fef9c3" : status.running ? "#dcfce7" : "#eef4f3";
  state.logs = status.logs || [];
  renderLogs();
  if (status.last_request && (!state.previewPinned || status.running || status.waiting_for_schedule)) {
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
  const monitorTarget = line.match(/监听下单第 \d+ 个请求（成功）：(.+)$/);
  if (monitorTarget) return monitorTarget[1];
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
  state.siteListSnapshot = null;
  applyParams(metadata.params);
  if (!$("scheduledStartInput").value) {
    $("scheduledStartInput").value = defaultScheduledStartValue();
  }
  $("newDateInput").value = state.snapshot.date;
  $("reservedDateInput").value = $("reservedDateInput").value || state.snapshot.date;
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
$("monitorEnabledInput").addEventListener("change", () => {
  if ($("monitorEnabledInput").checked) {
    $("monitorDateInput").value = $("monitorDateInput").value || state.selectedDates[0] || $("dateInput").value;
  } else {
    state.monitorCells = [];
  }
  state.siteStatusQueried = false;
  state.siteListSnapshot = null;
  renderChoices();
  preview();
});
$("querySiteStatusBtn").addEventListener("click", () => {
  querySiteStatus().catch((error) => {
    $("siteStatusSummary").textContent = `查询失败：${error.message}`;
  });
});
$("queryReservedBtn").addEventListener("click", () => {
  queryReservedStatus().catch((error) => {
    $("reservedLookupSummary").textContent = `查询失败：${error.message}`;
  });
});
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
  if (!$("monitorDateInput").value) {
    $("monitorDateInput").value = date;
  }
  if (!$("reservedDateInput").value) {
    $("reservedDateInput").value = date;
  }
  renderChoices();
  preview();
});

for (const id of ["intervalInput", "maxAttemptsInput", "dryRunInput", "verifySslInput", "scheduleEnabledInput", "scheduledStartInput", "monitorIntervalInput", "wxTokenInput", "shopIdInput", "brandCodeInput"]) {
  $(id).addEventListener("change", preview);
}
$("wxTokenInput").addEventListener("input", cacheWxToken);
$("reservedDateInput").addEventListener("change", () => {
  $("reservedDateInput").value = normalizeDate($("reservedDateInput").value);
});
$("monitorDateInput").addEventListener("change", () => {
  const date = normalizeDate($("monitorDateInput").value);
  $("monitorDateInput").value = date;
  state.siteStatusQueried = false;
  state.siteListSnapshot = null;
  state.monitorCells = [];
  renderChoices();
  preview();
});

$("dateInput").addEventListener("change", () => {
  const date = normalizeDate($("dateInput").value);
  if (date) {
    state.selectedDates = normalizeDates([date, ...state.selectedDates]);
    $("dateInput").value = state.selectedDates[0] || "";
    if (!$("monitorDateInput").value) {
      $("monitorDateInput").value = date;
    }
    if (!$("reservedDateInput").value) {
      $("reservedDateInput").value = date;
    }
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
