"""The fact log and everything derived from it.

A fact is something one node observed or a parent did: DNS activity, traffic
volume, a page visit, a DHCP lease, a lock. Facts are identified by
(origin node, sequence number), so replicating them between nodes is just
"send me what you have after N" and applying them twice is harmless.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from . import alerts as watch
from .categories import CATEGORY_LABELS, Catalogue
from .db import DB

log = logging.getLogger("bw.facts")

BUCKET = 300  # seconds
ACTIVE_BYTES = 100_000  # a minute with at least this much traffic counts as "in use"
FALLBACK_SECONDS_PER_QUERY = 30  # devices we can't meter: estimate from DNS activity
ALERT_THRESHOLD = 3  # blocked attempts in an hour before a parent hears about it

SEVERITY_BLOCK_REASONS = {"adult": ("high", "Tried to reach blocked adult content"),
                          "bypass": ("medium", "Tried to reach a VPN, proxy or filter-bypass site"),
                          "gambling": ("medium", "Tried to reach a gambling site")}


class Facts:
    def __init__(self, db: DB, node_id: str, catalogue: Catalogue, is_primary: bool = True,
                 notify: Callable[[dict[str, Any]], None] | None = None):
        self.db = db
        self.node_id = node_id
        self.cat = catalogue
        self.is_primary = is_primary
        self.notify = notify
        if self.db.meta_get("installed_at") is None:
            self.db.meta_set("installed_at", time.time())

    # ------------------------------------------------------------------ log
    def append(self, kind: str, data: Any, ts: float | None = None) -> int:
        """Record a fact observed on this node and apply it locally."""
        ts = time.time() if ts is None else ts
        with self.db.tx() as c:
            seq = (self.db.val("SELECT MAX(seq) FROM facts WHERE origin=?", (self.node_id,), 0) or 0) + 1
            c.execute("INSERT INTO facts(origin,seq,ts,kind,data) VALUES(?,?,?,?,?)",
                      (self.node_id, seq, ts, kind, json.dumps(data, separators=(",", ":"))))
            self._apply(self.node_id, seq, ts, kind, data)
        return seq

    def vector(self) -> dict[str, int]:
        return {r["origin"]: r["m"] for r in self.db.q("SELECT origin, MAX(seq) AS m FROM facts GROUP BY origin")}

    def since(self, vector: dict[str, int], limit: int = 2000) -> list[dict[str, Any]]:
        """Facts the caller (holding `vector`) has not seen yet, oldest first per origin."""
        out: list[dict[str, Any]] = []
        for origin in [r["origin"] for r in self.db.q("SELECT DISTINCT origin FROM facts")]:
            rows = self.db.q("SELECT origin,seq,ts,kind,data FROM facts WHERE origin=? AND seq>? ORDER BY seq LIMIT ?",
                             (origin, int(vector.get(origin, 0)), limit - len(out)))
            out.extend(dict(r) for r in rows)
            if len(out) >= limit:
                break
        return out

    def ingest(self, facts: list[dict[str, Any]]) -> int:
        """Apply facts pulled from a peer. Returns how many were new."""
        new = 0
        for f in facts:
            if f["origin"] == self.node_id:
                continue
            with self.db.tx() as c:
                cur = c.execute("INSERT OR IGNORE INTO facts(origin,seq,ts,kind,data) VALUES(?,?,?,?,?)",
                                (f["origin"], f["seq"], f["ts"], f["kind"], f["data"]))
                if cur.rowcount:
                    new += 1
                    try:
                        self._apply(f["origin"], f["seq"], f["ts"], f["kind"], json.loads(f["data"]))
                    except Exception:  # one bad fact must not wedge replication
                        log.exception("failed to apply fact %s/%s", f["origin"], f["seq"])
        return new

    def prune(self, fact_days: int = 14, detail_days: int = 30, rollup_days: int = 90) -> None:
        now = time.time()
        with self.db.tx() as c:
            c.execute("DELETE FROM facts WHERE ts<? AND seq < (SELECT MAX(seq) FROM facts f2 WHERE f2.origin=facts.origin)",
                      (now - fact_days * 86400,))
            c.execute("DELETE FROM events WHERE ts<?", (now - detail_days * 86400,))
            c.execute("DELETE FROM alerts WHERE ts<?", (now - rollup_days * 86400,))
            c.execute("DELETE FROM activity WHERE bucket<?", (int(now - rollup_days * 86400),))
            c.execute("DELETE FROM usage WHERE minute<?", (int(now - detail_days * 86400),))

    # ---------------------------------------------------------------- apply
    def _apply(self, origin: str, seq: int, ts: float, kind: str, data: Any) -> None:
        handler = getattr(self, f"_apply_{kind}", None)
        if handler is None:
            log.warning("unknown fact kind %r", kind)
            return
        handler(origin, seq, ts, data)

    def _touch(self, c, mac: str, ts: float) -> None:
        c.execute("INSERT INTO seen(mac,ts) VALUES(?,?) ON CONFLICT(mac) DO UPDATE SET ts=MAX(ts,excluded.ts)", (mac, ts))

    def _alert(self, origin: str, key: str, ts: float, mac: str, kind: str, severity: str, title: str,
               detail: str = "") -> None:
        c = self.db.conn()
        cur = c.execute("INSERT OR IGNORE INTO alerts(key,ts,mac,kind,severity,title,detail) VALUES(?,?,?,?,?,?,?)",
                        (key, ts, mac, kind, severity, title, detail))
        if cur.rowcount and self.notify and origin == self.node_id and time.time() - ts < 900:
            try:
                self.notify({"mac": mac, "kind": kind, "severity": severity, "title": title, "detail": detail, "ts": ts})
            except Exception:
                log.exception("notify failed")

    def _apply_dns(self, origin: str, seq: int, ts: float, data: dict[str, Any]) -> None:
        """rows: [mac, bucket, site, category, queries, blocked, reason]"""
        c = self.db.conn()
        for mac, bucket, site, category, queries, blocked, reason in data["rows"]:
            c.execute(
                "INSERT INTO activity(mac,bucket,site,category,queries,blocked,reason) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(mac,bucket,site) DO UPDATE SET queries=queries+excluded.queries, "
                "blocked=blocked+excluded.blocked, category=excluded.category, "
                "reason=CASE WHEN excluded.reason!='' THEN excluded.reason ELSE reason END",
                (mac, bucket, site, category, queries, blocked, reason))
            c.execute("INSERT OR IGNORE INTO dirty(mac,bucket) VALUES(?,?)", (mac, bucket))
            self._touch(c, mac, bucket + BUCKET if bucket + BUCKET < ts else ts)
            if blocked and reason in SEVERITY_BLOCK_REASONS:
                hour = bucket // 3600
                total = self.db.val("SELECT SUM(blocked) FROM activity WHERE mac=? AND reason=? AND bucket>=? AND bucket<?",
                                    (mac, reason, hour * 3600, (hour + 1) * 3600), 0)
                if total >= ALERT_THRESHOLD:
                    sev, title = SEVERITY_BLOCK_REASONS[reason]
                    self._alert(origin, f"{reason}:{mac}:{hour}", bucket, mac, f"blocked_{reason}", sev, title,
                                self.cat.label(site))

    def _apply_usage(self, origin: str, seq: int, ts: float, data: dict[str, Any]) -> None:
        """rows: [mac, minute, bytes_up, bytes_down]"""
        c = self.db.conn()
        for mac, minute, up, down in data["rows"]:
            c.execute("INSERT INTO usage(mac,minute,up,down) VALUES(?,?,?,?) ON CONFLICT(mac,minute) DO UPDATE SET "
                      "up=up+excluded.up, down=down+excluded.down", (mac, minute, up, down))
            c.execute("INSERT OR IGNORE INTO dirty(mac,bucket) VALUES(?,?)", (mac, minute - minute % BUCKET))
            if up + down >= ACTIVE_BYTES:
                self._touch(c, mac, minute + 60)

    def _apply_web(self, origin: str, seq: int, ts: float, data: dict[str, Any]) -> None:
        """items from the inspecting proxy: {t, mac, k: visit|search|video|block|pinned, host, ...}"""
        c = self.db.conn()
        custom = self.db.setting("watch_terms", [])
        for idx, item in enumerate(data["items"]):
            item = dict(item)
            t, mac, kind, host = item.pop("t", ts), item.pop("mac"), item.pop("k"), item.pop("host", "")
            cur = c.execute("INSERT OR IGNORE INTO events(origin,seq,idx,ts,mac,kind,host,data) VALUES(?,?,?,?,?,?,?,?)",
                            (origin, seq, idx, t, mac, kind, host, json.dumps(item, separators=(",", ":"))))
            if not cur.rowcount:
                continue
            self._touch(c, mac, t)
            if kind == "search":
                hit = watch.check(item.get("q", ""), custom)
                if hit:
                    cat, sev, _ = hit
                    self._alert(origin, f"search:{origin}:{seq}:{idx}", t, mac, f"search_{cat}", sev,
                                watch.CATEGORY_TITLES[cat], f"“{item.get('q', '')}” on {item.get('engine', host)}")
            elif kind == "block":
                self._alert(origin, f"kw:{origin}:{seq}:{idx}", t, mac, "keyword_block", "medium",
                            "Blocked by one of your keywords", f"“{item.get('keyword', '')}” — {host}")

    def _apply_lease(self, origin: str, seq: int, ts: float, data: dict[str, Any]) -> None:
        c = self.db.conn()
        mac, ip, hostname = data["mac"].lower(), data.get("ip", ""), data.get("hostname", "") or ""
        if data.get("event") == "del":
            c.execute("DELETE FROM leases WHERE mac=? AND ts<?", (mac, ts))
            return
        c.execute("INSERT INTO leases(mac,ip,hostname,ts,expires) VALUES(?,?,?,?,?) ON CONFLICT(mac) DO UPDATE SET "
                  "ip=excluded.ip, hostname=CASE WHEN excluded.hostname!='' THEN excluded.hostname ELSE hostname END, "
                  "ts=excluded.ts, expires=excluded.expires WHERE excluded.ts>=leases.ts",
                  (mac, ip, hostname, ts, data.get("expires", 0)))
        known = self.db.one("SELECT id, hostname FROM devices WHERE mac=?", (mac,))
        if known is None:
            if self.is_primary:
                c.execute("INSERT OR IGNORE INTO devices(mac,name,hostname,ip,created) VALUES(?,?,?,?,?)",
                          (mac, hostname or "", hostname, ip, ts))
                self.db.bump_config()
            if ts > (self.db.meta_get("installed_at", 0) or 0) + 6 * 3600:
                self._alert(origin, f"newdev:{mac}", ts, mac, "new_device", "info", "New device joined the network",
                            hostname or mac)
        elif self.is_primary and hostname and not known["hostname"]:
            c.execute("UPDATE devices SET hostname=? WHERE id=?", (hostname, known["id"]))
            self.db.bump_config()

    def _apply_note(self, origin: str, seq: int, ts: float, data: dict[str, Any]) -> None:
        self._alert(origin, data["key"], ts, data.get("mac", ""), data["kind"], data.get("severity", "info"),
                    data["title"], data.get("detail", ""))

    def _apply_action(self, origin: str, seq: int, ts: float, data: dict[str, Any]) -> None:
        c = self.db.conn()
        typ = data["type"]
        if typ in ("lock", "unlock"):
            c.execute("INSERT INTO locks(target,locked,until,ts) VALUES(?,?,?,?) ON CONFLICT(target) DO UPDATE SET "
                      "locked=excluded.locked, until=excluded.until, ts=excluded.ts WHERE excluded.ts>=locks.ts",
                      (data["target"], 1 if typ == "lock" else 0, float(data.get("until") or 0), ts))
        elif typ == "bonus":
            c.execute("INSERT INTO grants(profile_id,until,ts) VALUES(?,?,?) ON CONFLICT(profile_id) DO UPDATE SET "
                      "until=excluded.until, ts=excluded.ts WHERE excluded.ts>=grants.ts",
                      (int(data["profile_id"]), float(data["until"]), ts))
        elif typ == "ack":
            c.execute("UPDATE alerts SET acked=1, acked_ts=? WHERE key=?", (ts, data["key"]))
        elif typ == "ack_all":
            c.execute("UPDATE alerts SET acked=1, acked_ts=? WHERE acked=0 AND ts<=?", (ts, ts))

    # ------------------------------------------------------- time attribution
    def recompute_dirty(self, limit: int = 400) -> int:
        """Work out 'seconds in use' for buckets whose inputs changed, and spread them over sites."""
        rows = self.db.q("SELECT mac,bucket FROM dirty ORDER BY bucket LIMIT ?", (limit,))
        for r in rows:
            with self.db.tx():
                self._recompute(r["mac"], r["bucket"])
        return len(rows)

    def _recompute(self, mac: str, bucket: int) -> None:
        c = self.db.conn()
        c.execute("DELETE FROM dirty WHERE mac=? AND bucket=?", (mac, bucket))
        usage = self.db.q("SELECT up+down AS b FROM usage WHERE mac=? AND minute>=? AND minute<?",
                          (mac, bucket, bucket + BUCKET))
        c.execute("DELETE FROM activity WHERE mac=? AND bucket=? AND queries=0 AND blocked=0", (mac, bucket))
        acts = self.db.q("SELECT site, category, queries, blocked FROM activity WHERE mac=? AND bucket=?", (mac, bucket))
        weights = {a["site"]: float(a["queries"] - a["blocked"]) for a in acts
                   if a["category"] != "system" and a["queries"] > a["blocked"]}
        if usage:
            active = 60.0 * sum(1 for u in usage if u["b"] >= ACTIVE_BYTES)
        else:
            active = min(float(BUCKET), FALLBACK_SECONDS_PER_QUERY * sum(weights.values()))
        carried: dict[str, tuple[str, float]] = {}
        if active and not weights:
            # Traffic but no fresh lookups (a long video, a game session): continue what was in use just before.
            prev = self.db.q("SELECT site, category, seconds FROM activity WHERE mac=? AND bucket=? AND seconds>0",
                             (mac, bucket - BUCKET))
            carried = {p["site"]: (p["category"], p["seconds"]) for p in prev}
            weights = {s: v[1] for s, v in carried.items()}
        c.execute("UPDATE activity SET seconds=0 WHERE mac=? AND bucket=?", (mac, bucket))
        total = sum(weights.values())
        if active and total:
            for site, w in weights.items():
                secs = round(active * w / total, 1)
                if site in carried:
                    c.execute("INSERT INTO activity(mac,bucket,site,category,seconds) VALUES(?,?,?,?,?) "
                              "ON CONFLICT(mac,bucket,site) DO UPDATE SET seconds=excluded.seconds",
                              (mac, bucket, site, carried[site][0], secs))
                else:
                    c.execute("UPDATE activity SET seconds=? WHERE mac=? AND bucket=? AND site=?", (secs, mac, bucket, site))
        # The next bucket may be carrying forward from this one.
        nxt = bucket + BUCKET
        if self.db.one("SELECT 1 FROM usage WHERE mac=? AND minute>=? AND minute<? LIMIT 1", (mac, nxt, nxt + BUCKET)) \
                and not self.db.one("SELECT 1 FROM activity WHERE mac=? AND bucket=? AND queries>blocked "
                                    "AND category!='system' LIMIT 1", (mac, nxt)):
            c.execute("INSERT OR IGNORE INTO dirty(mac,bucket) VALUES(?,?)", (mac, nxt))


def category_label(cat: str) -> str:
    return CATEGORY_LABELS.get(cat, cat.title())
