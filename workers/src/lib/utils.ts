export function formatTime(): string {
  const now = new Date();
  const h = String(now.getHours()).padStart(2, "0");
  const m = String(now.getMinutes()).padStart(2, "0");
  const s = String(now.getSeconds()).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

export function formatTimestamp(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function formatSeconds(seconds: number): string {
  const remaining = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(remaining / 3600);
  const minutes = Math.floor((remaining % 3600) / 60);
  const secs = remaining % 60;
  if (hours) return `${hours}小时${minutes}分${secs}秒`;
  if (minutes) return `${minutes}分${secs}秒`;
  return `${secs}秒`;
}

export function parseScheduledTime(value: string): number | null {
  const text = value.trim().replace("T", " ");
  if (!text) return null;

  let normalized = text;
  if (!text.includes(" ") && text.includes(":")) {
    const today = new Date();
    const y = today.getFullYear();
    const m = String(today.getMonth() + 1).padStart(2, "0");
    const d = String(today.getDate()).padStart(2, "0");
    normalized = `${y}-${m}-${d} ${text}`;
  }

  const replaced = normalized.replace(/\//g, "-");
  const parsed = Date.parse(replaced.replace(" ", "T"));
  if (isNaN(parsed)) return null;
  return parsed;
}

export function uniqueDates(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const v of values) {
    const text = v.trim();
    if (text && !seen.has(text)) {
      seen.add(text);
      result.push(text);
    }
  }
  return result;
}

export function parseDate(value: string): Date {
  const normalized = value.replace(/-/g, "/");
  const parts = normalized.split("/");
  return new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
}
