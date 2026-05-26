export interface Env {
  REQUEST_STORE: KVNamespace;
  ADMIN_KV: KVNamespace;
  BOOKING_CLIENT: DurableObjectNamespace;
  ADMIN_AUTH: DurableObjectNamespace;
  WECHAT_BOT_WEBHOOK: string;
}

export interface Params {
  request_file?: string;
  dry_run: boolean;
  verify_ssl: boolean;
  interval_seconds: number;
  max_attempts: number;
  schedule_enabled: boolean;
  scheduled_start_at: string;
  date: string;
  dates: string[];
  request_mode?: string;
  selections?: Selection[];
  monitor_enabled: boolean;
  monitor_date: string;
  monitor_interval_seconds: number;
  monitor_selections: Selection[];
  courts: CourtOption[];
  time_slots: TimeSlot[];
  headers: Record<string, string>;
}

export interface Selection {
  court: CourtOption;
  time_slot: TimeSlot;
}

export interface CourtOption {
  site_id: number;
  site_name: string;
}

export interface TimeSlot {
  start_time: string;
  end_time: string;
  start_timestamp: number;
  end_timestamp: number;
  price: string;
  times: string;
  source_date?: string;
}

export interface VenueSnapshot {
  venues_id: string;
  date: string;
  dates: string[];
  courts: CourtOption[];
  times: TimeSlot[];
  selected_site_id: number | null;
  selected_site_name: string;
  fixed_courts: CourtOption[];
  selected_times: TimeSlot[];
}

export interface RequestData {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: Record<string, any> | null;
}

export interface CaptureEntry {
  path: string;
  url: string;
  hostname?: string;
  order?: number;
  req: { method: string; headers: Record<string, string>; base64?: string };
  res?: { base64?: string };
}

export interface RuntimeStatus {
  running: boolean;
  params: Record<string, any>;
  logs: string[];
  last_request: Record<string, any> | null;
  last_response: Record<string, any> | null;
  waiting_for_schedule: boolean;
  scheduled_start_at: string;
}

export interface ResponseData {
  success: boolean;
  index?: number;
  target?: string;
  request?: Record<string, any>;
  message?: string;
  error?: string;
  dry_run?: boolean;
  body?: Record<string, any>;
  payload?: Record<string, any>;
  notification_sent?: boolean;
}
