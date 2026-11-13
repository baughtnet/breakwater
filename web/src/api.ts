import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(path, {
    method: init?.method ?? (init?.body !== undefined ? "POST" : "GET"),
    headers: init?.body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    credentials: "same-origin",
  });
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      /* keep the generic message */
    }
    if (res.status === 401 && !path.startsWith("/api/login")) window.dispatchEvent(new Event("bw:signed-out"));
    throw new ApiError(res.status, message);
  }
  return (await res.json()) as T;
}

/** Fetch with optional polling. Keeps showing the previous data while a refresh is in flight. */
export function useData<T>(path: string | null, intervalMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const current = useRef(path);
  current.current = path;

  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      const d = await api<T>(path);
      if (current.current === path) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (current.current === path) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (current.current === path) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    reload();
    if (!intervalMs) return;
    const id = window.setInterval(() => {
      if (!document.hidden) reload();
    }, intervalMs);
    return () => window.clearInterval(id);
  }, [reload, intervalMs]);

  return { data, error, loading, reload };
}

// ------------------------------------------------------------------ types
export type Tier = "strict" | "basic" | "none";
export type Monitoring = "full" | "sites" | "off";
export type Day = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
export const DAYS: Day[] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

export interface Session {
  authenticated: boolean;
  username: string | null;
  role: "primary" | "standby";
  node_id: string;
  setup_needed: boolean;
  tz: string;
  utc_offset: number;
}

export interface Policy {
  safesearch: boolean;
  youtube: "off" | "moderate" | "strict";
  blocked_services: string[];
  homework: { enabled: boolean; services: string[]; windows: Record<Day, [string, string][]> };
  bedtime: { enabled: boolean; days: Record<Day, [string, string] | null> };
  daily_limit: { enabled: boolean; minutes: Record<Day, number | null> };
  allow_domains: string[];
  block_domains: string[];
  keywords: string[];
}

export interface Profile {
  id: number;
  name: string;
  kind: "kid" | "adult";
  color: string;
  tier: Tier;
  monitoring: Monitoring;
  policy: Policy;
}

export interface Device {
  id: number;
  mac: string;
  name: string;
  hostname: string;
  kind: string;
  profile_id: number | null;
  ip: string;
  static_ip: boolean;
  route_setting: "gateway" | "router" | null;
  inspect_setting: boolean;
  overrides: Partial<Policy>;
  online: boolean;
  last_seen: number;
  used_seconds: number;
  created: number;
  tier: Tier;
  monitoring: Monitoring;
  route: "gateway" | "router";
  inspect: boolean;
  locked: boolean;
  lock_reason: "" | "manual" | "bedtime" | "limit";
  lock_until: number;
  grant_until: number;
  homework: boolean;
  known: boolean;
}

export interface SiteRef {
  site: string;
  label: string;
  app: string | null;
}

export interface Alert {
  id: number;
  key: string;
  ts: number;
  mac: string;
  kind: string;
  severity: "info" | "medium" | "high";
  title: string;
  detail: string;
  acked: boolean;
  device: string;
  profile_id: number | null;
  profile: string | null;
  color: string | null;
}

export interface OverviewProfile {
  id: number;
  name: string;
  kind: "kid" | "adult";
  color: string;
  tier: Tier;
  monitoring: Monitoring;
  devices: Device[];
  online: boolean;
  used_seconds: number;
  limit_minutes: number | null;
  locked: boolean;
  lock_reason: string;
  lock_until: number;
  grant_until: number;
  homework: boolean;
  categories: { category: string; label: string; seconds: number }[];
  top: (SiteRef & { category: string; seconds: number })[];
  hours: number[];
  week: number[];
  blocked: number;
  alerts: number;
}

export interface Overview {
  now: number;
  day_start: number;
  week_starts: number[];
  profiles: OverviewProfile[];
  unassigned: Device[];
  alerts: Alert[];
  unacked: number;
}

export interface Activity {
  monitoring: Monitoring;
  day: string;
  day_start: number;
  day_end: number;
  timeline: ({ slot: number; t: number } & Record<string, number>)[];
  categories: { category: string; label: string; seconds: number }[];
  sites: (SiteRef & { category: string; seconds: number; queries: number; blocked: number })[];
  trend: ({ day_start: number; date: string; total: number } & Record<string, number | string>)[];
  heatmap: number[][];
  blocked: (SiteRef & { ts: number; reason: string; count: number; mac: string })[];
  total_seconds: number;
  data_bytes: number;
  first_active: number | null;
  last_active: number | null;
}

export interface WebEvent {
  id: number;
  ts: number;
  mac: string;
  host: string;
  url?: string;
  title?: string;
  q?: string;
  engine?: string;
  vid?: string;
  channel?: string;
  dur?: number;
  keyword?: string;
}

export interface Service {
  id: string;
  name: string;
  group: string;
  icon: string;
}
export interface Catalogue {
  services: Service[];
  groups: { id: string; services: string[] }[];
}

export interface NodeStatus {
  node_id: string;
  role: string;
  online: boolean;
  self: boolean;
  active: boolean;
  gateway: boolean;
  dhcp: boolean;
  proxy: boolean;
  ipv6_leak: boolean;
  ip: string;
  uptime: number;
  dns: Record<string, { ok: boolean; error: string }>;
}
export interface SystemInfo {
  nodes: NodeStatus[];
  lists: { tier: string; name: string; rules: number; updated: string; enabled: boolean }[];
  role: string;
  node_id: string;
  counts: Record<string, number>;
  pinned: { host: string; mac: string; n: number; ts: number }[];
  network: { lan: string; router: string; vip: string };
  ca_available: boolean;
}
export interface HouseSettings {
  quarantine_unknown: boolean;
  ntfy_url: string;
  watch_terms: string[];
  passthrough_hosts: string[];
  detail_days: number;
  rollup_days: number;
}
