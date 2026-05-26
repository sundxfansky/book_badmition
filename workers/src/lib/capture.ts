import type { CaptureEntry, CourtOption, Params, RequestData, TimeSlot, VenueSnapshot } from "../types";
import { parseDate } from "./utils";

const SUBMIT_PATH = "/v2/reserve/submit?";
const SITE_LIST_PREFIX = "/v1/venues/venues_site_list";
const CALENDAR_PREFIX = "/v1/venues/calendar";

export const FIXED_COURTS: CourtOption[] = [
  { site_id: 3692729935134806, site_name: "1号场" },
  { site_id: 3692729935134807, site_name: "2号场" },
  { site_id: 3692729935134808, site_name: "3号场" },
  { site_id: 3692729935134809, site_name: "4号场" },
  { site_id: 3692729935134810, site_name: "5号场" },
  { site_id: 3692729935134811, site_name: "6号场" },
  { site_id: 3704542185717805, site_name: "7号场" },
  { site_id: 3713773110689834, site_name: "6楼V1号场" },
  { site_id: 3713764789190689, site_name: "6楼V2号场" },
];

export const FIXED_SOURCE_DATE = "2026/05/28";

export const FIXED_TIME_SLOTS: TimeSlot[] = [
  { start_time: "07:00", end_time: "08:00", start_timestamp: 1779922800, end_timestamp: 1779926400, price: "75", times: "1" },
  { start_time: "08:00", end_time: "09:00", start_timestamp: 1779926400, end_timestamp: 1779930000, price: "75", times: "1" },
  { start_time: "09:00", end_time: "10:00", start_timestamp: 1779930000, end_timestamp: 1779933600, price: "75", times: "1" },
  { start_time: "10:00", end_time: "11:00", start_timestamp: 1779933600, end_timestamp: 1779937200, price: "75", times: "1" },
  { start_time: "11:00", end_time: "12:00", start_timestamp: 1779937200, end_timestamp: 1779940800, price: "75", times: "1" },
  { start_time: "12:00", end_time: "13:00", start_timestamp: 1779940800, end_timestamp: 1779944400, price: "75", times: "1" },
  { start_time: "13:00", end_time: "14:00", start_timestamp: 1779944400, end_timestamp: 1779948000, price: "75", times: "1" },
  { start_time: "14:00", end_time: "15:00", start_timestamp: 1779948000, end_timestamp: 1779951600, price: "130", times: "1" },
  { start_time: "15:00", end_time: "16:00", start_timestamp: 1779951600, end_timestamp: 1779955200, price: "130", times: "1" },
  { start_time: "16:00", end_time: "17:00", start_timestamp: 1779955200, end_timestamp: 1779958800, price: "130", times: "1" },
  { start_time: "17:00", end_time: "18:00", start_timestamp: 1779958800, end_timestamp: 1779962400, price: "130", times: "1" },
  { start_time: "18:00", end_time: "19:00", start_timestamp: 1779962400, end_timestamp: 1779966000, price: "130", times: "1" },
  { start_time: "19:00", end_time: "20:00", start_timestamp: 1779966000, end_timestamp: 1779969600, price: "130", times: "1" },
  { start_time: "20:00", end_time: "21:00", start_timestamp: 1779969600, end_timestamp: 1779973200, price: "130", times: "1" },
  { start_time: "21:00", end_time: "22:00", start_timestamp: 1779973200, end_timestamp: 1779976800, price: "130", times: "1" },
];

export class CaptureStore {
  entries: CaptureEntry[];

  constructor(entries: CaptureEntry[]) {
    this.entries = entries;
  }

  static async fromKV(kv: KVNamespace, key = "request_template:default"): Promise<CaptureStore> {
    const raw = await kv.get(key);
    if (!raw) throw new Error("No request template in KV. Upload request.txt first.");
    return new CaptureStore(JSON.parse(raw));
  }

  submitEntry(): CaptureEntry {
    const entry = this.entries.find((e) => e.path === SUBMIT_PATH);
    if (!entry) throw new Error(`No request found for path: ${SUBMIT_PATH}`);
    return entry;
  }

  submitBody(): Record<string, any> {
    return decodeJsonBody(this.submitEntry().req);
  }

  submitHeaders(): Record<string, string> {
    const headers = { ...this.submitEntry().req.headers };
    delete headers["host"];
    delete headers["content-length"];
    delete headers["accept-encoding"];
    headers["content-type"] = "application/json";
    return headers;
  }

  latestSiteListEntry(): CaptureEntry | null {
    const entries = this.entries.filter(
      (e) => e.path?.startsWith(SITE_LIST_PREFIX) && e.hostname === "stmember.styd.cn"
    );
    if (!entries.length) return null;
    return entries.reduce((a, b) => ((a.order || 0) > (b.order || 0) ? a : b));
  }

  siteListEntry(): CaptureEntry {
    const entry = this.latestSiteListEntry();
    if (!entry) throw new Error("No venues_site_list request found");
    return entry;
  }

  siteListHeaders(): Record<string, string> {
    const headers = { ...this.siteListEntry().req.headers };
    delete headers["host"];
    delete headers["content-length"];
    delete headers["accept-encoding"];
    return headers;
  }

  availableDates(): string[] {
    const dates: string[] = [];
    for (const entry of this.entries) {
      const path = entry.path || "";
      if (path.startsWith(CALENDAR_PREFIX) && entry.res) {
        const payload = decodeJsonBody(entry.res);
        const data = payload?.data;
        if (data?.list) {
          for (const item of data.list) {
            if (item.date) dates.push(String(item.date));
          }
        }
      }
      if (path.startsWith(SITE_LIST_PREFIX)) {
        const url = new URL(path, "http://x");
        const date = url.searchParams.get("date");
        if (date) dates.push(decodeURIComponent(date));
      }
    }
    const submitDate = String(this.submitBody().venues_date || "");
    if (submitDate) dates.push(submitDate);
    return unique(dates);
  }

  venueSnapshot(): VenueSnapshot {
    const body = this.submitBody();
    const courts = FIXED_COURTS;
    const submitDate = String(body.venues_date || "");
    const selectedTimes: TimeSlot[] = (body.venues_site_time || []).map((item: any) => ({
      start_time: String(item.start_time || ""),
      end_time: String(item.end_time || ""),
      start_timestamp: Number(item.start_timestamp || 0),
      end_timestamp: Number(item.end_timestamp || 0),
      price: String(item.price || "0"),
      times: String(item.times || "1"),
      source_date: String(item.source_date || submitDate),
    }));
    const selectedSiteId = body.venues_site_time?.[0]?.site_id ? Number(body.venues_site_time[0].site_id) : null;
    const selectedSiteName = body.venues_site_time?.[0]?.site_name ? String(body.venues_site_time[0].site_name) : "";
    const times: TimeSlot[] = FIXED_TIME_SLOTS.map((t) => ({ ...t, source_date: FIXED_SOURCE_DATE }));

    return {
      venues_id: String(body.venues_id || ""),
      date: submitDate,
      dates: this.availableDates(),
      courts,
      times,
      selected_site_id: selectedSiteId,
      selected_site_name: selectedSiteName,
      fixed_courts: selectedSiteId && selectedSiteName ? [{ site_id: selectedSiteId, site_name: selectedSiteName }] : [],
      selected_times: selectedTimes,
    };
  }

  buildSiteListRequest(params: Partial<Params>, date: string): RequestData {
    const entry = this.siteListEntry();
    const headers = { ...this.siteListHeaders(), ...(params.headers || {}) };
    const parsed = new URL(entry.url);
    parsed.searchParams.set("date", date);
    return { method: entry.req.method || "GET", url: parsed.toString(), headers, body: null };
  }

  buildSubmitRequest(params: Partial<Params> & { date?: string; court?: CourtOption; time_slots?: TimeSlot[] }): RequestData {
    const body = { ...this.submitBody() };
    const headers = { ...this.submitHeaders(), ...(params.headers || {}) };
    const venuesDate = params.date || body.venues_date;
    const court = params.court || firstCourt(params as any) || ({} as any);
    let timeSlots = (params.time_slots || []).slice(0, 2);
    if (!timeSlots.length) timeSlots = (body.venues_site_time || []).slice(0, 2);

    const siteId = court.site_id || (timeSlots[0] as any)?.site_id;
    const siteName = court.site_name || (timeSlots[0] as any)?.site_name;

    body.venues_date = venuesDate;
    body.venues_site_time = timeSlots.map((slot: any) => ({
      site_id: Number(slot.site_id || siteId),
      site_name: String(slot.site_name || siteName),
      start_time: String(slot.start_time),
      start_timestamp: shiftedTimestamp(slot, venuesDate, "start_timestamp"),
      end_timestamp: shiftedTimestamp(slot, venuesDate, "end_timestamp"),
      end_time: String(slot.end_time),
      times: String(slot.times || "1"),
      price: String(slot.price || "0"),
    }));

    return {
      method: this.submitEntry().req.method || "POST",
      url: this.submitEntry().url,
      headers,
      body,
    };
  }

  buildSubmitRequests(params: Partial<Params>): RequestData[] {
    const selections = (params as any).selections || [];
    if (selections.length) return this.buildSelectionRequests(params, selections);

    let courts = (params as any).courts || [];
    if (!courts.length && (params as any).court) courts = [(params as any).court];
    if (!courts.length) courts = [firstCourt(params as any)].filter(Boolean);
    let dates = params.dates || [];
    if (!dates.length && params.date) dates = [params.date];
    if (!dates.length) dates = [String(this.submitBody().venues_date || "")];

    const requests: RequestData[] = [];
    for (const date of dates) {
      for (const court of courts) {
        if (court) requests.push(this.buildSubmitRequest({ ...params, date, court } as any));
      }
    }
    return requests;
  }

  private buildSelectionRequests(params: Partial<Params>, selections: any[]): RequestData[] {
    let dates = params.dates || [];
    if (!dates.length && params.date) dates = [params.date];
    if (!dates.length) dates = [String(this.submitBody().venues_date || "")];

    const mode = (params as any).request_mode || "single";
    const groups = selectionGroups(selections, mode);
    const requests: RequestData[] = [];
    for (const date of dates) {
      for (const group of groups) {
        const court = group[0].court;
        const timeSlots = group.map((item: any) => item.time_slot);
        requests.push(this.buildSubmitRequest({ ...params, date, court, time_slots: timeSlots } as any));
      }
    }
    return requests;
  }
}

function decodeJsonBody(section: { base64?: string }): Record<string, any> {
  const encoded = section.base64;
  if (!encoded) return {};
  const decoded = atob(encoded);
  const bytes = new Uint8Array(decoded.length);
  for (let i = 0; i < decoded.length; i++) bytes[i] = decoded.charCodeAt(i);
  const text = new TextDecoder().decode(bytes);
  return JSON.parse(text);
}

function shiftedTimestamp(slot: any, targetDate: string, key: string): number {
  const original = Number(slot[key]);
  const sourceDate = String(slot.source_date || slot.date || "");
  if (!sourceDate) return original;
  try {
    const source = parseDate(sourceDate);
    const target = parseDate(targetDate);
    return original + Math.floor((target.getTime() - source.getTime()) / 1000);
  } catch {
    return original;
  }
}

function firstCourt(params: any): CourtOption | null {
  const courts = params.courts || [];
  if (courts.length) return courts[0];
  return params.court || null;
}

function selectionGroups(selections: any[], mode: string): any[][] {
  const normalized = selections
    .filter((item: any) => item.court && item.time_slot)
    .sort((a: any, b: any) => {
      const aKey = `${a.court.site_id}-${a.time_slot.start_timestamp}`;
      const bKey = `${b.court.site_id}-${b.time_slot.start_timestamp}`;
      return aKey.localeCompare(bKey);
    });

  if (mode !== "pair") return normalized.map((item: any) => [item]);

  const groups: any[][] = [];
  const used = new Set<number>();
  for (let i = 0; i < normalized.length; i++) {
    if (used.has(i)) continue;
    used.add(i);
    const pair = [normalized[i]];
    for (let j = i + 1; j < normalized.length; j++) {
      if (used.has(j)) continue;
      const sameCourt = String(normalized[i].court.site_id) === String(normalized[j].court.site_id);
      const adjacent = Number(normalized[i].time_slot.end_timestamp) === Number(normalized[j].time_slot.start_timestamp);
      if (sameCourt && adjacent) {
        pair.push(normalized[j]);
        used.add(j);
        break;
      }
    }
    groups.push(pair);
  }
  return groups;
}

function unique(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const v of values) {
    if (v && !seen.has(v)) {
      seen.add(v);
      result.push(v);
    }
  }
  return result;
}

export function requestSummary(requestData: RequestData): Record<string, any> {
  const headers = { ...requestData.headers };
  if (headers["wx-token"]) {
    const v = headers["wx-token"];
    headers["wx-token"] = v.length <= 8 ? "*".repeat(v.length) : `${v.slice(0, 4)}...${v.slice(-4)}`;
  }
  return { method: requestData.method, url: requestData.url, headers, body: requestData.body };
}

export function siteListPayloadToDict(payload: any, date: string): { date: string; items: any[] } {
  if (!payload || typeof payload !== "object") return { date: decodeURIComponent(date || ""), items: [] };
  const data = payload.data || {};
  const items: any[] = [];
  for (const court of data.list || []) {
    const courtInfo = { site_id: court.site_id, site_name: court.site_name };
    for (const slot of court.site_data || []) {
      items.push({
        court: courtInfo,
        time_slot: {
          start_time: String(slot.start_time || ""),
          end_time: String(slot.end_time || ""),
          start_timestamp: Number(slot.start_timestamp || 0),
          end_timestamp: Number(slot.end_timestamp || 0),
          price: String(slot.price || "0"),
          times: String(slot.times || "1"),
          source_date: date,
        },
        status: slot.status,
        available: String(slot.status) === "2" && String(slot.times || "0") !== "0",
        disabled_desc: String(slot.disabled_desc || ""),
        disabled_reason: String(slot.disabled_reason || ""),
        member_name: String(slot.member_name || ""),
        mobile: String(slot.mobile || ""),
      });
    }
  }
  return { date: decodeURIComponent(date || ""), items };
}
