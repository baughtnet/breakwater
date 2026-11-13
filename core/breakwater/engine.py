"""The running brain of one node: resolves policy, feeds the gateway and AdGuard, ingests what they observe."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import httpx

from . import adguard, policy
from .categories import catalogue
from .config import Config
from .db import DB, row_dict
from .facts import BUCKET, Facts

log = logging.getLogger("bw.engine")

# Hosts never worth decrypting: they pin certificates, carry bulk video, or belong to the OS.
PASSTHROUGH_HOSTS = [
    "googlevideo.com", "nflxvideo.net", "nflximg.net", "ttvnw.net", "akamaihd.net", "apple.com", "icloud.com",
    "mzstatic.com", "windowsupdate.com", "update.microsoft.com", "delivery.mp.microsoft.com", "live.com",
    "microsoftonline.com", "xboxlive.com", "playstation.com", "playstation.net", "nintendo.net", "nintendo.com",
    "steampowered.com", "steamcontent.com", "epicgames.com", "roblox.com", "rbxcdn.com", "discord.gg",
    "discord.media", "whatsapp.net", "signal.org", "snapchat.com", "sc-cdn.net", "tiktokv.com", "tiktokcdn.com",
    "spotify.com", "scdn.co", "zoom.us", "teams.microsoft.com", "gvt1.com", "gvt2.com", "play.googleapis.com",
    "android.clients.google.com", "mtalk.google.com", "connectivitycheck.gstatic.com", "paypal.com", "interac.ca",
]


class Engine:
    def __init__(self, cfg: Config, db: DB | None = None):
        self.cfg = cfg
        self.db = db or DB(cfg.db_path)
        self.cat = catalogue()
        self.facts = Facts(self.db, cfg.node_id, self.cat, cfg.is_primary, notify=self._notify)
        self.instances: dict[str, adguard.Instance] = {}
        if cfg.adguard_enabled:
            self.instances = {
                "strict": adguard.Instance("strict", cfg.adguard_strict_url, cfg.adguard_user, cfg.adguard_password),
                "basic": adguard.Instance("basic", cfg.adguard_basic_url, cfg.adguard_user, cfg.adguard_password),
            }
        self.gateway_status: dict[str, Any] = {}
        self._eff_cache: tuple[float, dict[str, policy.Effective]] = (0, {})
        self._sync_sig: dict[str, tuple[str, float]] = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.started = time.time()

    # ----------------------------------------------------------------- clock
    def now(self) -> datetime:
        return datetime.now(self.cfg.tz)

    def day_start(self, day: datetime | None = None) -> int:
        d = day or self.now()
        return int(d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    # ---------------------------------------------------------------- config
    def profiles(self) -> list[dict[str, Any]]:
        return [row_dict(r, ("policy",)) for r in self.db.q("SELECT * FROM profiles ORDER BY sort, id")]

    def devices(self) -> list[dict[str, Any]]:
        leases = {r["mac"]: r for r in self.db.q("SELECT * FROM leases")}
        out = []
        for r in self.db.q("SELECT * FROM devices ORDER BY id"):
            d = row_dict(r, ("overrides",))
            lease = leases.get(d["mac"])
            d["addr"] = d["ip"] or (lease["ip"] if lease else "")
            d["hostname"] = d["hostname"] or (lease["hostname"] if lease else "")
            out.append(d)
        return out

    def quarantine(self) -> bool:
        return bool(self.db.setting("quarantine_unknown", True))

    def ip_to_mac(self) -> dict[str, str]:
        m: dict[str, str] = {}
        for r in self.db.q("SELECT ip, mac FROM neigh WHERE ts>?", (time.time() - 86400,)):
            m[r["ip"]] = r["mac"]
        for r in self.db.q("SELECT ip, mac FROM leases"):
            m[r["ip"]] = r["mac"]
        for r in self.db.q("SELECT ip, mac FROM devices WHERE ip IS NOT NULL AND ip!=''"):
            m[r["ip"]] = r["mac"]
        return m

    # ---------------------------------------------------------------- policy
    def used_today(self) -> dict[str, float]:
        return {r["mac"]: r["s"] or 0 for r in self.db.q(
            "SELECT mac, SUM(seconds) AS s FROM activity WHERE bucket>=? GROUP BY mac", (self.day_start(),))}

    def effective_all(self, max_age: float = 1.5) -> dict[str, policy.Effective]:
        """Effective policy for every known device, keyed by MAC."""
        stamp, cached = self._eff_cache
        if time.time() - stamp < max_age:
            return cached
        now = self.now()
        profiles = {p["id"]: p for p in self.profiles()}
        devices = self.devices()
        used = self.used_today()
        locks = {r["target"]: dict(r) for r in self.db.q("SELECT * FROM locks")}
        grants = {r["profile_id"]: r["until"] for r in self.db.q("SELECT * FROM grants")}
        by_profile: dict[int, float] = defaultdict(float)
        for d in devices:
            if d["profile_id"]:
                by_profile[d["profile_id"]] += used.get(d["mac"], 0)
        quarantine = self.quarantine()
        out: dict[str, policy.Effective] = {}
        for d in devices:
            prof = profiles.get(d["profile_id"]) if d["profile_id"] else None
            out[d["mac"]] = policy.evaluate(
                d, prof, now, used_seconds=by_profile.get(d["profile_id"], 0),
                device_lock=locks.get(f"d:{d['mac']}"),
                profile_lock=locks.get(f"p:{d['profile_id']}") if prof else None,
                grant_until=grants.get(d["profile_id"], 0) if prof else 0,
                quarantine_unknown=quarantine, groups=self.cat.groups)
        self._eff_cache = (time.time(), out)
        return out

    def invalidate(self) -> None:
        self._eff_cache = (0, {})

    # --------------------------------------------------------------- gateway
    def gateway_state(self) -> dict[str, Any]:
        eff = self.effective_all()
        profiles = {p["id"]: p["name"] for p in self.profiles()}
        devs = []
        for d in self.devices():
            e = eff[d["mac"]]
            devs.append({
                "mac": d["mac"], "ip": d["addr"], "static": bool(d["ip"]),
                "name": d["name"] or d["hostname"] or d["mac"],
                "profile": profiles.get(d["profile_id"], ""), "known": e.known,
                "tier": e.tier, "route": e.route, "inspect": e.inspect, "monitoring": e.monitoring,
                "locked": e.locked, "lock_reason": e.lock_reason, "lock_until": e.lock_until,
                "keywords": e.keywords, "safesearch": e.safesearch, "youtube": e.youtube,
            })
        state = {
            "quarantine": self.quarantine(),
            "devices": devs,
            "passthrough": sorted(set(PASSTHROUGH_HOSTS) | set(self.db.setting("passthrough_hosts", []))),
            "tz": self.cfg.tz_name,
        }
        state["version"] = hashlib.sha1(json.dumps(state, sort_keys=True).encode()).hexdigest()[:16]
        return state

    def report(self, payload: dict[str, Any]) -> None:
        """Everything the gateway (agent or proxy) tells us."""
        if "status" in payload:
            self.gateway_status = {**payload["status"], "ts": time.time()}
        if payload.get("neigh"):
            now = time.time()
            with self.db.tx() as c:
                for row in payload["neigh"]:
                    ip, mac = row[0], row[1].lower()
                    c.execute("INSERT INTO neigh(ip,mac,ts) VALUES(?,?,?) ON CONFLICT(ip) DO UPDATE SET "
                              "mac=excluded.mac, ts=excluded.ts", (ip, mac, now))
                    if len(row) > 2 and row[2]:  # on the LAN right now: counts as online even if unmonitored
                        c.execute("INSERT INTO seen(mac,ts) VALUES(?,?) ON CONFLICT(mac) DO UPDATE SET "
                                  "ts=MAX(ts,excluded.ts)", (mac, now))
            self._discover(payload["neigh"])
        for f in payload.get("facts") or []:
            if f["kind"] in ("usage", "lease", "web"):
                self.facts.append(f["kind"], f["data"], f.get("ts"))
        if payload.get("facts"):
            self.invalidate()

    def _infrastructure_ips(self) -> set[str]:
        ips = {self.cfg.router_ip, self.cfg.node_ip, self.cfg.vip}
        for peer in self.cfg.peers:
            ips.add(peer.split("//")[-1].split(":")[0].split("/")[0])
        return {ip for ip in ips if ip}

    def _discover(self, neigh: list[list[Any]]) -> None:
        """Devices that never asked us for an address (static IPs, or the router still doing DHCP) are
        found by being seen on the LAN, and recorded the same way a lease would be."""
        skip = self._infrastructure_ips()
        known = {r["mac"] for r in self.db.q("SELECT mac FROM leases UNION SELECT mac FROM devices")}
        for row in neigh:
            ip, mac = row[0], row[1].lower()
            if ip in skip or mac in known or not (len(row) > 2 and row[2]):
                continue
            known.add(mac)
            self.facts.append("lease", {"mac": mac, "ip": ip, "hostname": "", "expires": 0, "event": "seen"})

    # --------------------------------------------------------------- actions
    def act(self, data: dict[str, Any]) -> None:
        self.facts.append("action", data)
        self.invalidate()

    def lock_until_morning(self, profile: dict[str, Any] | None) -> float:
        pol = policy.merged_policy(profile, None) if profile else policy.default_policy("kid")
        return policy.next_bedtime_end(pol, self.now()).timestamp()

    # --------------------------------------------------------------- adguard
    def sync_adguard(self, force: bool = False) -> None:
        if not self.instances:
            return
        eff = self.effective_all()
        per: dict[str, list[dict[str, Any]]] = {"strict": [], "basic": []}
        for d in self.devices():
            if not d["addr"]:
                continue
            e = eff[d["mac"]]
            per["strict" if e.tier == "strict" else "basic"].append(
                {"mac": d["mac"], "ip": d["addr"], "eff": e.as_dict(), "soft_lock": e.locked and e.route == "router"})
        for tier, inst in self.instances.items():
            sig = hashlib.sha1(json.dumps(per[tier], sort_keys=True, default=str).encode()).hexdigest()
            last_sig, last_ts = self._sync_sig.get(tier, ("", 0))
            if not force and sig == last_sig and time.time() - last_ts < 300:
                continue
            try:
                inst.sync(per[tier])
                self._sync_sig[tier] = (sig, time.time())
                inst.last_error, inst.last_ok = "", time.time()
            except Exception as exc:
                inst.last_error = str(exc)[:300]
                log.warning("adguard %s sync failed: %s", tier, exc)

    def ingest_dns(self) -> int:
        """Pull new query-log entries from both instances and record them as one fact per instance."""
        total = 0
        ip_mac = self.ip_to_mac()
        eff = self.effective_all()
        for tier, inst in self.instances.items():
            key = f"dns_cursor_{tier}"
            try:
                reasons = inst.list_reasons()
                items, cursor = inst.pull_log(float(self.db.meta_get(key, 0) or 0))
            except Exception as exc:
                inst.last_error = str(exc)[:300]
                continue
            agg: dict[tuple[str, int, str], list[Any]] = {}
            for it in items:
                q = it.get("question") or {}
                if q.get("type") not in ("A", "AAAA", "HTTPS"):
                    continue
                mac = ip_mac.get(it.get("client", ""))
                if not mac:
                    continue
                e = eff.get(mac)
                if e is not None and e.monitoring == "off":
                    continue
                reason_raw = it.get("reason", "")
                blocked = reason_raw.startswith("Filtered") and reason_raw != "FilteredSafeSearch"
                reason = ""
                if blocked:
                    rule = (it.get("rules") or [{}])[0]
                    if rule.get("text", "").startswith("/.*/"):
                        continue  # the device is paused; retries while locked are not "attempts"
                    reason = {"FilteredParental": "adult", "FilteredSafeBrowsing": "malware",
                              "FilteredBlockedService": "service"}.get(reason_raw) \
                        or reasons.get(rule.get("filter_list_id", 0), "custom")
                site, category = self.cat.classify(q.get("name", ""))
                bucket = int(it["ts"]) - int(it["ts"]) % BUCKET
                row = agg.setdefault((mac, bucket, site), [category, 0, 0, ""])
                row[1] += 1
                if blocked:
                    row[2] += 1
                    row[3] = reason
            if agg:
                self.facts.append("dns", {"rows": [[m, b, s, *v] for (m, b, s), v in agg.items()]})
                total += len(items)
            if cursor:
                self.db.meta_set(key, cursor)
        return total

    # ---------------------------------------------------------- notifications
    def _notify(self, alert: dict[str, Any]) -> None:
        url = self.db.setting("ntfy_url", "")
        if not url or alert["severity"] == "info":
            return
        who = self.device_label(alert["mac"])
        threading.Thread(target=_post_ntfy, args=(url, alert, who), daemon=True).start()

    def device_label(self, mac: str) -> str:
        r = self.db.one("SELECT d.name, d.hostname, p.name AS pname FROM devices d LEFT JOIN profiles p "
                        "ON p.id=d.profile_id WHERE d.mac=?", (mac,))
        if not r:
            return mac
        dev = r["name"] or r["hostname"] or mac
        return f"{r['pname']} · {dev}" if r["pname"] else dev

    # ----------------------------------------------------------------- loops
    def tick_limits(self) -> None:
        """Raise a one-per-day note when a profile runs out of time."""
        eff = self.effective_all()
        today = self.now().strftime("%Y-%m-%d")
        seen: set[int] = set()
        for d in self.devices():
            e = eff[d["mac"]]
            pid = d["profile_id"]
            if pid and e.lock_reason == "limit" and pid not in seen:
                seen.add(pid)
                key = f"limit:{pid}:{today}"
                if not self.db.one("SELECT 1 FROM alerts WHERE key=?", (key,)):
                    self.facts.append("note", {"key": key, "mac": d["mac"], "kind": "limit_reached", "severity": "info",
                                               "title": "Daily time limit reached",
                                               "detail": f"{e.limit_minutes} minutes used"})

    def run_background(self) -> None:
        for fn, every in ((self._loop_fast, 2), (self._loop_ingest, 10), (self._loop_slow, 20), (self._loop_cluster, 3)):
            threading.Thread(target=self._runner, args=(fn, every), daemon=True, name=fn.__name__).start()

    def stop(self) -> None:
        self._stop.set()

    def _runner(self, fn, every: float) -> None:
        while not self._stop.is_set():
            try:
                fn()
            except Exception:
                log.exception("%s failed", fn.__name__)
            self._stop.wait(every)

    def _loop_fast(self) -> None:
        self.sync_adguard()
        self.tick_limits()

    def _loop_ingest(self) -> None:
        self.ingest_dns()
        self.facts.recompute_dirty()

    def _loop_slow(self) -> None:
        self.facts.recompute_dirty(2000)
        if time.time() - (self.db.meta_get("last_prune", 0) or 0) > 3600:
            self.facts.prune(detail_days=int(self.db.setting("detail_days", 30)),
                             rollup_days=int(self.db.setting("rollup_days", 90)))
            self.db.meta_set("last_prune", time.time())

    # --------------------------------------------------------------- cluster
    def status(self) -> dict[str, Any]:
        gw = self.gateway_status
        gw_ok = bool(gw) and time.time() - gw.get("ts", 0) < 15
        return {
            "node_id": self.cfg.node_id, "role": self.cfg.role, "ts": time.time(), "uptime": time.time() - self.started,
            "config_version": self.db.config_version(),
            "gateway": gw_ok, "active": bool(gw_ok and gw.get("vip_held")),
            "dhcp": bool(gw_ok and gw.get("dhcp")), "proxy": bool(gw_ok and gw.get("proxy")),
            "ipv6_leak": bool(gw.get("ipv6_leak")) if gw_ok else False,
            "ip": self.cfg.node_ip,
            "dns": ({t: {"ok": not i.last_error and (i.last_ok > 0), "error": i.last_error}
                     for t, i in self.instances.items()}
                    or ({"strict": {"ok": True, "error": ""}, "basic": {"ok": True, "error": ""}} if self.cfg.demo else {})),
        }

    def _loop_cluster(self) -> None:
        if not self.cfg.peers:
            return
        me = self.status()
        headers = {"X-BW-Token": self.cfg.token}
        for peer in self.cfg.peers:
            try:
                r = httpx.post(f"{peer}/cluster/pull", json={"vector": self.facts.vector(), "status": me},
                               headers=headers, timeout=8)
                r.raise_for_status()
                body = r.json()
                if self.facts.ingest(body.get("facts") or []):
                    self.invalidate()
                self.record_node(body["status"])
                is_primary_peer = body["status"].get("role") == "primary"
                if not self.cfg.is_primary and is_primary_peer and \
                        body["status"]["config_version"] != self.db.config_version():
                    snap = httpx.get(f"{peer}/cluster/config", headers=headers, timeout=8).json()
                    self.db.load_config_snapshot(snap)
                    self.invalidate()
                    log.info("config synced to version %s", snap["version"])
            except Exception as exc:
                log.debug("peer %s unreachable: %s", peer, exc)

    def record_node(self, status: dict[str, Any]) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO nodes(node_id,ts,status) VALUES(?,?,?) ON CONFLICT(node_id) DO UPDATE SET "
                      "ts=excluded.ts, status=excluded.status", (status["node_id"], time.time(), json.dumps(status)))

    def nodes(self) -> list[dict[str, Any]]:
        out = [dict(self.status(), self=True, online=True)]
        for r in self.db.q("SELECT * FROM nodes WHERE node_id!=?", (self.cfg.node_id,)):
            st = json.loads(r["status"])
            st["online"] = time.time() - r["ts"] < 15
            st["self"] = False
            if not st["online"]:
                st["active"] = False
            out.append(st)
        return out


def _post_ntfy(url: str, alert: dict[str, Any], who: str) -> None:
    try:
        httpx.post(url, content=f"{who}: {alert['detail']}".encode(),
                   headers={"Title": f"Breakwater: {alert['title']}",
                            "Priority": "high" if alert["severity"] == "high" else "default",
                            "Tags": "ocean"}, timeout=8)
    except Exception as exc:
        log.warning("ntfy failed: %s", exc)


def local_midnights(tz, days: int, end: datetime | None = None) -> list[int]:
    """Epoch starts of the last `days` local days, oldest first (DST-safe)."""
    end = end or datetime.now(tz)
    base = end.replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for i in range(days - 1, -1, -1):
        d = (base - timedelta(days=i)).replace(hour=0)
        out.append(int(datetime(d.year, d.month, d.day, tzinfo=tz).timestamp()))
    return out
