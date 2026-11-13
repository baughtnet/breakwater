export const CATEGORY_ORDER = ["streaming", "social", "games", "learning", "chat", "shopping", "ai", "other"] as const;
export const CATEGORY_LABEL: Record<string, string> = {
  streaming: "Video & music",
  social: "Social",
  games: "Games",
  learning: "Learning",
  chat: "Chat",
  shopping: "Shopping",
  ai: "AI",
  other: "Browsing",
};
export const catColor = (c: string) => `var(--c-${CATEGORY_LABEL[c] ? c : "other"})`;
/** Categories whose fill is light enough that a label printed on it must be dark. */
export const DARK_LABEL = new Set(["learning"]);

export function duration(seconds: number, style: "short" | "long" = "short"): string {
  const m = Math.round(seconds / 60);
  if (m < 1) return style === "long" ? "Less than a minute" : "0m";
  const h = Math.floor(m / 60);
  const rest = m % 60;
  if (!h) return `${rest}m`;
  return rest ? `${h}h ${rest}m` : `${h}h`;
}

export function bytes(n: number): string {
  if (n < 1e6) return `${Math.max(0, Math.round(n / 1e3))} KB`;
  if (n < 1e9) return `${(n / 1e6).toFixed(n < 1e7 ? 1 : 0)} MB`;
  return `${(n / 1e9).toFixed(1)} GB`;
}

// Schedules are enforced in the house's time zone, so every time on screen is shown in house time too.
// The server tells us its UTC offset; we never rely on the browser's own time-zone data.
let houseOffset = -new Date().getTimezoneOffset() * 60;
export function setHouseOffset(seconds: number) {
  houseOffset = seconds;
}
/** A Date whose UTC fields read as house-local time. Format it with timeZone "UTC". */
export function houseDate(ts: number = Date.now() / 1000): Date {
  return new Date((ts + houseOffset) * 1000);
}
const fmt = (ts: number, opts: Intl.DateTimeFormatOptions) => houseDate(ts).toLocaleString([], { ...opts, timeZone: "UTC" });
/** Hour of the house's day right now, as a fraction (20.5 = 8:30pm). */
export function houseHour(): number {
  const d = houseDate();
  return d.getUTCHours() + d.getUTCMinutes() / 60;
}
/** Monday = 0. */
export function houseWeekday(ts: number = Date.now() / 1000): number {
  return (houseDate(ts).getUTCDay() + 6) % 7;
}
export function houseToday(): string {
  return houseDate().toISOString().slice(0, 10);
}

export function clock(ts: number): string {
  return fmt(ts, { hour: "numeric", minute: "2-digit" });
}

const HOUR12 = new Intl.DateTimeFormat([], { hour: "numeric" }).resolvedOptions().hour12 !== false;
/** Compact hour for axes and tooltips, following the viewer's 12- or 24-hour habit. */
export function hourLabel(h: number): string {
  h = ((h % 24) + 24) % 24;
  if (!HOUR12) return `${String(h).padStart(2, "0")}:00`;
  if (h === 0) return "12a";
  if (h === 12) return "12p";
  return h < 12 ? `${h}a` : `${h - 12}p`;
}

export function dayLabel(ts: number, opts: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric" }): string {
  return fmt(ts, opts);
}

export function ago(ts: number, now = Date.now() / 1000): string {
  const s = Math.max(0, now - ts);
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.round(s / 86400)} d ago`;
  return dayLabel(ts, { month: "short", day: "numeric" });
}

export function when(ts: number): string {
  const sameDay = houseDate(ts).toISOString().slice(0, 10) === houseToday();
  return sameDay ? clock(ts) : `${dayLabel(ts, { month: "short", day: "numeric" })}, ${clock(ts)}`;
}

export function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export const REASON_LABEL: Record<string, string> = {
  adult: "Adult content",
  bypass: "VPN or filter bypass",
  gambling: "Gambling",
  malware: "Malware or phishing",
  service: "Blocked app",
  custom: "Your block list",
  ads: "Ads and trackers",
};

export function lockSentence(reason: string, until: number): string {
  const till = until ? ` until ${clock(until)}` : "";
  if (reason === "bedtime") return `Bedtime${till}`;
  if (reason === "limit") return "Out of time for today";
  return `Paused${till}`;
}
