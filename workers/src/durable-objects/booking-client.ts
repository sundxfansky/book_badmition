import type { Env, Params, RequestData, ResponseData } from "../types";
import { CaptureStore, requestSummary, siteListPayloadToDict } from "../lib/capture";
import { sendWechatNotification } from "../lib/notifier";
import { formatTime, formatSeconds, parseScheduledTime, uniqueDates } from "../lib/utils";

export class BookingClientDO implements DurableObject {
  private ctx: DurableObjectState;
  private env: Env;
  private capture: CaptureStore | null = null;

  constructor(ctx: DurableObjectState, env: Env) {
    this.ctx = ctx;
    this.env = env;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    switch (url.pathname) {
      case "/api/metadata": return this.handleMetadata();
      case "/api/status": return this.handleStatus();
      case "/api/export": return this.handleExport();
      case "/api/preview": return this.handlePreview(request);
      case "/api/save": return this.handleSave(request);
      case "/api/import": return this.handleSave(request);
      case "/api/start": return this.handleStart(request);
      case "/api/stop": return this.handleStop();
      case "/api/clear-logs": return this.handleClearLogs();
      case "/api/site-status": return this.handleSiteStatus(request);
      default: return new Response("Not found", { status: 404 });
    }
  }

  async alarm(): Promise<void> {
    const running = await this.ctx.storage.get<boolean>("running");
    if (!running) return;

    const params = (await this.ctx.storage.get<Params>("params")) as Params;
    if (!params) { await this.stop(); return; }

    const scheduledTs = params.schedule_enabled ? parseScheduledTime(params.scheduled_start_at) : null;
    if (scheduledTs && Date.now() < scheduledTs) {
      const remaining = Math.ceil((scheduledTs - Date.now()) / 1000);
      await this.log(`定时启动倒计时：距离开始还有 ${formatSeconds(remaining)}`);
      await this.ctx.storage.put("waiting_for_schedule", true);
      this.ctx.storage.setAlarm(new Date(Math.min(scheduledTs, Date.now() + 60000)));
      return;
    }

    await this.ctx.storage.put("waiting_for_schedule", false);

    if (params.monitor_enabled) {
      await this.runMonitorRound(params);
    } else {
      await this.runSubmitRound(params);
    }
  }

  private async runSubmitRound(params: Params): Promise<void> {
    const attempt = ((await this.ctx.storage.get<number>("attempt")) || 0) + 1;
    await this.ctx.storage.put("attempt", attempt);
    await this.log(`第 ${attempt} 轮提交预约请求`);

    const capture = await this.loadCapture();
    const requests = capture.buildSubmitRequests(params);
    if (!requests.length) {
      await this.log("没有可提交的请求");
      await this.stop();
      return;
    }

    await this.log(`本轮并发提交 ${requests.length} 个请求`);
    const responses = await Promise.all(
      requests.map((req, i) => this.sendRequest(req, params, i + 1))
    );

    const successfulKeys = new Set<string>();
    let successUnits = 0;
    for (const resp of responses) {
      if (resp.success) {
        const keys = requestSlotKeys(resp.request || {});
        for (const key of keys) {
          if (!successfulKeys.has(key)) { successfulKeys.add(key); successUnits++; }
        }
      }
    }

    await this.ctx.storage.put("last_response", { success: successUnits >= 2, success_units: successUnits, responses });

    if (successUnits >= 2) {
      await this.log(`成功时间数已达到 ${successUnits}，任务结束`);
      await this.notifySuccess(params, { success: true, success_units: successUnits, responses });
      await this.stop();
      return;
    }

    const maxAttempts = params.max_attempts || 0;
    if (maxAttempts && attempt >= maxAttempts) {
      await this.log("达到最大尝试次数，任务结束");
      await this.stop();
      return;
    }

    const interval = Math.max(100, (params.interval_seconds || 0.1) * 1000);
    this.ctx.storage.setAlarm(new Date(Date.now() + interval));
  }

  private async runMonitorRound(params: Params): Promise<void> {
    const attempt = ((await this.ctx.storage.get<number>("attempt")) || 0) + 1;
    await this.ctx.storage.put("attempt", attempt);
    await this.log(`第 ${attempt} 轮监听场地释放`);

    const capture = await this.loadCapture();
    const targets = monitorTargets(params);
    if (!targets.length) {
      await this.log("监听下单未选择目标");
      await this.stop();
      return;
    }

    const dates = monitorDates(params);
    const released: any[] = [];
    const successfulKeys = (await this.ctx.storage.get<string[]>("successful_slot_keys")) || [];
    const successfulSet = new Set(successfulKeys);

    for (const date of dates) {
      const reqData = capture.buildSiteListRequest(params, date);
      await this.log(`查询场地状态：${date}`);
      const siteResp = await this.sendSiteListRequest(reqData, params);
      if (!siteResp.success) {
        await this.log(`查询场地状态失败：${date} ${siteResp.error || siteResp.message || ""}`);
        continue;
      }
      const available = availableMonitorTargets(siteResp.payload || {}, targets, date);
      for (const item of available) {
        const key = selectionKey(item.date, item.court, item.time_slot);
        if (!successfulSet.has(key)) released.push(item);
      }
    }

    if (!released.length) {
      await this.log("本轮没有监听目标释放");
      const maxAttempts = params.max_attempts || 0;
      if (maxAttempts && attempt >= maxAttempts) {
        await this.log("达到最大监听次数，任务结束");
        await this.stop();
        return;
      }
      const interval = Math.max(100, (params.monitor_interval_seconds || 20) * 1000);
      this.ctx.storage.setAlarm(new Date(Date.now() + interval));
      return;
    }

    await this.log(`发现 ${released.length} 个监听目标可预约，立即下单`);
    const submitResponses = await Promise.all(
      released.map((item, i) => {
        const reqData = capture.buildSubmitRequest({
          ...params, date: item.date, court: item.court, time_slots: [item.time_slot],
        } as any);
        return this.sendRequest(reqData, params, i + 1);
      })
    );

    let successUnits = 0;
    for (const resp of submitResponses) {
      if (resp.success) {
        const keys = requestSlotKeys(resp.request || {});
        for (const key of keys) {
          if (!successfulSet.has(key)) { successfulSet.add(key); successUnits++; }
        }
      }
    }
    await this.ctx.storage.put("successful_slot_keys", [...successfulSet]);

    if (successUnits > 0) {
      await this.log("监听下单任务结束");
      await this.notifySuccess(params, { success: true, success_units: successUnits, responses: submitResponses });
      await this.stop();
      return;
    }

    const maxAttempts = params.max_attempts || 0;
    if (maxAttempts && attempt >= maxAttempts) {
      await this.log("达到最大监听次数，任务结束");
      await this.stop();
      return;
    }
    const interval = Math.max(100, (params.monitor_interval_seconds || 20) * 1000);
    this.ctx.storage.setAlarm(new Date(Date.now() + interval));
  }

  private async handleMetadata(): Promise<Response> {
    const capture = await this.loadCapture();
    const snapshot = capture.venueSnapshot();
    const params = (await this.ctx.storage.get<any>("params")) || this.defaultParams(capture);
    const siteListEntry = capture.latestSiteListEntry();
    let siteListSnapshot = { date: "", items: [] as any[] };
    if (siteListEntry?.res) {
      const url = new URL(siteListEntry.path || "", "http://x");
      const date = url.searchParams.get("date") || "";
      const payload = decodeJsonBodyFromEntry(siteListEntry.res);
      siteListSnapshot = siteListPayloadToDict(payload, date);
    }
    return json({ snapshot, site_list_snapshot: siteListSnapshot, params: withoutWxToken(params) });
  }

  private async handleStatus(): Promise<Response> {
    const params = (await this.ctx.storage.get<any>("params")) || {};
    const logs = (await this.ctx.storage.get<string[]>("logs")) || [];
    const running = (await this.ctx.storage.get<boolean>("running")) || false;
    const lastRequest = await this.ctx.storage.get<any>("last_request");
    const lastResponse = await this.ctx.storage.get<any>("last_response");
    const waitingForSchedule = (await this.ctx.storage.get<boolean>("waiting_for_schedule")) || false;
    const scheduledStartAt = (await this.ctx.storage.get<string>("scheduled_start_at")) || "";
    return json({
      running, params: withoutWxToken(params), logs: logs.slice(-300),
      last_request: lastRequest || null, last_response: lastResponse || null,
      waiting_for_schedule: waitingForSchedule, scheduled_start_at: scheduledStartAt,
    });
  }

  private async handleExport(): Promise<Response> {
    const params = (await this.ctx.storage.get<any>("params")) || {};
    return json(withoutWxToken(params));
  }

  private async handlePreview(request: Request): Promise<Response> {
    const body = (await request.json()) as any;
    const capture = await this.loadCapture();
    const params = await this.mergedParams(body, capture);
    if (params.monitor_enabled) {
      const dates = monitorDates(params);
      const statusRequests = dates.map((date) => safeRequestSummary(capture.buildSiteListRequest(params, date)));
      const targets = monitorTargets(params);
      const submitRequests = targets.map((item) =>
        safeRequestSummary(capture.buildSubmitRequest({ ...params, date: item.date, court: item.court, time_slots: [item.time_slot] } as any))
      );
      return json({ mode: "monitor", count: statusRequests.length, requests: statusRequests, monitor_targets: targets.map(monitorTargetDesc), submit_requests_when_released: submitRequests });
    }
    const requests = capture.buildSubmitRequests(params);
    return json({ count: requests.length, requests: requests.map(safeRequestSummary) });
  }

  private async handleSave(request: Request): Promise<Response> {
    const body = (await request.json()) as any;
    const capture = await this.loadCapture();
    const params = await this.mergedParams(body, capture);
    await this.rememberWxToken(body);
    await this.ctx.storage.put("params", withoutWxToken(params));
    await this.log("已更新抢票参数");
    return this.handleStatus();
  }

  private async handleStart(request: Request): Promise<Response> {
    const body = (await request.json()) as any;
    const capture = await this.loadCapture();
    const params = await this.mergedParams(body, capture);
    await this.rememberWxToken(body);

    const running = await this.ctx.storage.get<boolean>("running");
    if (running) return this.handleStatus();

    const scheduledTs = params.schedule_enabled ? parseScheduledTime(params.scheduled_start_at) : null;
    if (params.schedule_enabled && scheduledTs === null) {
      await this.log("定时启动时间格式不正确");
      return this.handleStatus();
    }

    await this.ctx.storage.put("params", withoutWxToken(params));
    await this.ctx.storage.put("running", true);
    await this.ctx.storage.put("attempt", 0);
    await this.ctx.storage.put("waiting_for_schedule", false);
    await this.ctx.storage.put("scheduled_start_at", params.scheduled_start_at || "");
    await this.ctx.storage.put("successful_slot_keys", []);

    if (scheduledTs && scheduledTs > Date.now()) {
      await this.ctx.storage.put("waiting_for_schedule", true);
      await this.log(`已设置定时启动：${params.scheduled_start_at}，等待 ${formatSeconds((scheduledTs - Date.now()) / 1000)} 后开始`);
      this.ctx.storage.setAlarm(new Date(Math.min(scheduledTs, Date.now() + 60000)));
    } else {
      await this.log("开始执行抢票任务");
      this.ctx.storage.setAlarm(new Date(Date.now() + 10));
    }

    await this.registerClient();
    return this.handleStatus();
  }

  private async handleStop(): Promise<Response> {
    await this.stop();
    return this.handleStatus();
  }

  private async handleClearLogs(): Promise<Response> {
    await this.ctx.storage.put("logs", []);
    return this.handleStatus();
  }

  private async handleSiteStatus(request: Request): Promise<Response> {
    const body = (await request.json()) as any;
    const capture = await this.loadCapture();
    const params = await this.mergedParams(body, capture);
    await this.rememberWxToken(body);
    const date = monitorDate(params);
    if (!date) {
      await this.log("请先选择监听日期");
      return json({ success: false, error: "请先选择监听日期", snapshot: { date: "", items: [] } });
    }
    const reqData = capture.buildSiteListRequest(params, date);
    await this.log(`查询当前场地预约情况：${date}`);
    const response = await this.sendSiteListRequest(reqData, params);
    if (!response.success) {
      const msg = response.error || response.message || "查询失败";
      await this.log(`查询当前场地预约情况失败：${date} ${msg}`);
      return json({ success: false, error: msg, request: safeRequestSummary(reqData), response, snapshot: { date, items: [] } });
    }
    const snapshot = siteListPayloadToDict(response.payload, date);
    const availableCount = snapshot.items.filter((i: any) => i.available).length;
    const occupiedCount = snapshot.items.length - availableCount;
    await this.log(`查询完成：${date}，可约 ${availableCount} 个，已约 ${occupiedCount} 个`);
    return json({ success: true, message: `查询完成`, request: safeRequestSummary(reqData), snapshot, available_count: availableCount, occupied_count: occupiedCount });
  }

  private async sendRequest(reqData: RequestData, params: Params, index: number): Promise<ResponseData> {
    await this.ctx.storage.put("last_request", safeRequestSummary(reqData));
    const target = requestTargetDesc(reqData);
    await this.log(`准备第 ${index} 个请求：${target}`);

    if (params.dry_run) {
      await this.log(`完成第 ${index} 个请求（成功）：${target}`);
      return { success: true, dry_run: true, index, target, request: safeRequestSummary(reqData), body: safeRequestSummary(reqData) };
    }

    try {
      const resp = await fetch(reqData.url, {
        method: reqData.method,
        headers: reqData.headers,
        body: reqData.body ? JSON.stringify(reqData.body) : undefined,
      });
      const payload = (await resp.json()) as any;
      const success = payload.code === 0;
      const status = success ? "成功" : "失败";
      await this.log(`完成第 ${index} 个请求（${status}）：${target}`);
      return { success, index, target, request: safeRequestSummary(reqData), message: payload.msg || String(payload.code), payload };
    } catch (e: any) {
      await this.log(`完成第 ${index} 个请求（失败）：${target} - ${e.message}`);
      return { success: false, index, target, request: safeRequestSummary(reqData), error: e.message };
    }
  }

  private async sendSiteListRequest(reqData: RequestData, params: Params): Promise<any> {
    try {
      const resp = await fetch(reqData.url, { method: reqData.method, headers: reqData.headers });
      const payload = (await resp.json()) as any;
      return { success: payload.code === 0, message: payload.msg || String(payload.code), payload };
    } catch (e: any) {
      return { success: false, error: e.message };
    }
  }

  private async stop(): Promise<void> {
    await this.ctx.storage.put("running", false);
    await this.ctx.storage.put("waiting_for_schedule", false);
    await this.ctx.storage.put("scheduled_start_at", "");
    await this.ctx.storage.deleteAlarm();
    await this.log("已请求停止");
  }

  private async log(message: string): Promise<void> {
    const line = `[${formatTime()}] ${message}`;
    const logs = (await this.ctx.storage.get<string[]>("logs")) || [];
    logs.push(line);
    if (logs.length > 300) logs.splice(0, logs.length - 300);
    await this.ctx.storage.put("logs", logs);
  }

  private async loadCapture(): Promise<CaptureStore> {
    if (!this.capture) {
      this.capture = await CaptureStore.fromKV(this.env.REQUEST_STORE);
    }
    return this.capture;
  }

  private defaultParams(capture: CaptureStore): any {
    const snapshot = capture.venueSnapshot();
    return {
      dry_run: false, verify_ssl: false, interval_seconds: 0.1, max_attempts: 100000,
      schedule_enabled: false, scheduled_start_at: "", date: snapshot.date, dates: [snapshot.date],
      monitor_enabled: false, monitor_date: snapshot.date, monitor_interval_seconds: 20, monitor_selections: [],
      courts: [{ site_id: snapshot.selected_site_id, site_name: snapshot.selected_site_name }],
      time_slots: snapshot.selected_times, headers: {},
    };
  }

  private async mergedParams(incoming: any, capture: CaptureStore): Promise<Params> {
    const stored = (await this.ctx.storage.get<any>("params")) || this.defaultParams(capture);
    const merged = JSON.parse(JSON.stringify(stored));
    for (const [key, value] of Object.entries(incoming || {})) {
      if (key === "headers") {
        merged.headers = { ...(merged.headers || {}), ...(value as any || {}) };
      } else if (key === "time_slots") {
        merged.time_slots = ((value as any) || []).slice(0, 2);
      } else if (key === "dates") {
        merged.dates = uniqueDates((value as any) || []);
      } else {
        (merged as any)[key] = value;
      }
    }
    const wxToken = await this.ctx.storage.get<string>("wx_token");
    if (wxToken && !(merged.headers || {})["wx-token"]) {
      merged.headers = merged.headers || {};
      merged.headers["wx-token"] = wxToken;
    }
    return merged as Params;
  }

  private async rememberWxToken(params: any): Promise<void> {
    const token = String((params?.headers || {})["wx-token"] || "").trim();
    if (token) await this.ctx.storage.put("wx_token", token);
  }

  private async notifySuccess(params: Params, response: any): Promise<void> {
    const message = params.dry_run
      ? `【羽毛球抢票】dry-run 演练完成\n成功时间数：${response.success_units || 0}`
      : `【羽毛球抢票】抢票成功\n成功时间数：${response.success_units || 0}`;
    await this.log(message);
    if (this.env.WECHAT_BOT_WEBHOOK && this.env.WECHAT_BOT_WEBHOOK !== "YOUR_KEY_HERE") {
      const result = await sendWechatNotification(this.env.WECHAT_BOT_WEBHOOK, message);
      await this.log(result);
    }
  }

  private async registerClient(): Promise<void> {
    const clientId = this.ctx.id.toString();
    const existing = await this.env.ADMIN_KV.get("active_clients");
    const clients: string[] = existing ? JSON.parse(existing) : [];
    if (!clients.includes(clientId)) {
      clients.push(clientId);
      await this.env.ADMIN_KV.put("active_clients", JSON.stringify(clients));
    }
  }
}

function decodeJsonBodyFromEntry(section: { base64?: string }): any {
  if (!section.base64) return {};
  const decoded = atob(section.base64);
  const bytes = new Uint8Array(decoded.length);
  for (let i = 0; i < decoded.length; i++) bytes[i] = decoded.charCodeAt(i);
  return JSON.parse(new TextDecoder().decode(bytes));
}

function withoutWxToken(params: any): any {
  const clean = JSON.parse(JSON.stringify(params || {}));
  if (clean.headers) delete clean.headers["wx-token"];
  return clean;
}

function safeRequestSummary(reqData: RequestData): any {
  const summary = requestSummary(reqData);
  if (summary.headers) delete summary.headers["wx-token"];
  return summary;
}

function requestTargetDesc(reqData: RequestData): string {
  const body = reqData.body || {};
  const date = body.venues_date || "未知日期";
  const slots = body.venues_site_time || [];
  if (!slots.length) return `${date} 未知场地 未知时间`;
  const courtName = String(slots[0].site_name || "未知场地");
  const timeRanges = slots.map((s: any) => `${s.start_time || "?"}-${s.end_time || "?"}`).join(", ");
  return `${date} ${courtName} ${timeRanges}`;
}

function requestSlotKeys(reqSummary: any): string[] {
  const body = reqSummary.body || {};
  const date = String(body.venues_date || "");
  return (body.venues_site_time || []).map((slot: any) =>
    [date, String(slot.site_id || ""), String(slot.start_time || ""), String(slot.end_time || "")].join("|")
  );
}

function monitorDate(params: any): string {
  const date = String(params.monitor_date || "").trim();
  if (date) return date;
  const dates = params.dates || [];
  if (dates.length) return String(dates[0]);
  return String(params.date || "").trim();
}

function monitorDates(params: any): string[] {
  const date = monitorDate(params);
  return date ? [date] : [];
}

function monitorTargets(params: any): any[] {
  const targets: any[] = [];
  for (const date of monitorDates(params)) {
    for (const item of params.monitor_selections || []) {
      if (item.court && item.time_slot) targets.push({ date, court: item.court, time_slot: item.time_slot });
    }
  }
  return targets;
}

function monitorTargetDesc(item: any): string {
  const court = item.court || {};
  const ts = item.time_slot || {};
  return `${item.date || ""} ${court.site_name || "未知场地"} ${ts.start_time || "?"}-${ts.end_time || "?"}`;
}

function selectionKey(date: string, court: any, timeSlot: any): string {
  return [date, String(court?.site_id || ""), String(timeSlot?.start_time || ""), String(timeSlot?.end_time || "")].join("|");
}

function availableMonitorTargets(payload: any, targets: any[], date: string): any[] {
  const data = payload?.data || {};
  const availableKeys = new Set<string>();
  for (const court of data.list || []) {
    const courtInfo = { site_id: court.site_id, site_name: court.site_name };
    for (const slot of court.site_data || []) {
      if (String(slot.status) === "2" && String(slot.times || "0") !== "0") {
        availableKeys.add(selectionKey(date, courtInfo, slot));
      }
    }
  }
  return targets.filter((item) => item.date === date && availableKeys.has(selectionKey(date, item.court, item.time_slot)));
}

function json(data: any): Response {
  return new Response(JSON.stringify(data), { headers: { "content-type": "application/json" } });
}
