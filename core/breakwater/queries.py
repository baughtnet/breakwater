"""Read-side queries that shape stored activity into what the dashboard draws."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from .categories import CATEGORIES, CATEGORY_LABELS
from .engine import Engine, local_midnights

ONLINE_WINDOW = 300
NOTABLE_REASONS = ("adult", "bypass", "gambling", "malware", "service", "custom")


def _marks(n: int) -> str:
    return ",".join("?" * n)


def site_info(eng: Engine, site: str) -> dict[str, Any]:
    return {"site": site, "label": eng.cat.label(site), "app": site[1:] if site.startswith("@") else None}


def device_view(eng: Engine, d: dict[str, Any], eff, seen: dict[str, float], used: dict[str, float]) -> dict[str, Any]:
    now = time.time()
    return {
        "id": d["id"], "mac": d["mac"], "name": d["name"] or d["hostname"] or d["mac"], "hostname": d["hostname"],
        "kind": d["kind"], "profile_id": d["profile_id"], "ip": d["addr"], "static_ip": bool(d["ip"]),
        "route_setting": d["route"], "inspect_setting": bool(d["inspect"]), "overrides": d["overrides"],
        "online": now - seen.get(d["mac"], 0) < ONLINE_WINDOW, "last_seen": seen.get(d["mac"], 0),
        "used_seconds": round(used.get(d["mac"], 0)), "created": d["created"],
        **{k: v for k, v in eff.as_dict().items() if k in (
            "tier", "monitoring", "route", "inspect", "locked", "lock_reason", "lock_until", "grant_until",
            "homework", "known")},
    }


def all_devices(eng: Engine) -> list[dict[str, Any]]:
    eff = eng.effective_all()
    seen = {r["mac"]: r["ts"] for r in eng.db.q("SELECT mac, ts FROM seen")}
    used = eng.used_today()
    return [device_view(eng, d, eff[d["mac"]], seen, used) for d in eng.devices()]


def _profile_macs(devices: list[dict[str, Any]], pid: int) -> list[str]:
    return [d["mac"] for d in devices if d["profile_id"] == pid]


def overview(eng: Engine) -> dict[str, Any]:
    db = eng.db
    start = eng.day_start()
    devices = all_devices(eng)
    by_mac = {d["mac"]: d for d in devices}
    profiles = eng.profiles()

    cat_rows = db.q("SELECT mac, category, SUM(seconds) AS s FROM activity WHERE bucket>=? AND seconds>0 "
                    "GROUP BY mac, category", (start,))
    site_rows = db.q("SELECT mac, site, category, SUM(seconds) AS s FROM activity WHERE bucket>=? AND seconds>0 "
                     "GROUP BY mac, site ORDER BY s DESC", (start,))
    hour_rows = db.q("SELECT mac, (bucket-?)/3600 AS h, SUM(seconds) AS s FROM activity WHERE bucket>=? AND seconds>0 "
                     "GROUP BY mac, h", (start, start))
    blocked_rows = db.q(f"SELECT mac, SUM(blocked) AS b FROM activity WHERE bucket>=? AND blocked>0 "
                        f"AND reason IN ({_marks(len(NOTABLE_REASONS))}) GROUP BY mac", (start, *NOTABLE_REASONS))
    alert_rows = db.q("SELECT mac, COUNT(*) AS n FROM alerts WHERE acked=0 GROUP BY mac")
    week = local_midnights(eng.cfg.tz, 7)
    week_rows = db.q("SELECT mac, bucket, seconds FROM activity WHERE bucket>=? AND seconds>0", (week[0],))

    out_profiles = []
    for p in profiles:
        macs = set(_profile_macs(devices, p["id"]))
        devs = [by_mac[m] for m in macs]
        cats: dict[str, float] = defaultdict(float)
        for r in cat_rows:
            if r["mac"] in macs and r["category"] != "system":
                cats[r["category"]] += r["s"]
        sites: dict[str, list[Any]] = {}
        for r in site_rows:
            if r["mac"] in macs:
                cur = sites.setdefault(r["site"], [r["category"], 0.0])
                cur[1] += r["s"]
        hours = [0.0] * 24
        for r in hour_rows:
            if r["mac"] in macs and 0 <= r["h"] < 24:
                hours[r["h"]] += r["s"]
        days = [0.0] * 7
        for r in week_rows:
            if r["mac"] in macs:
                idx = max(i for i, s in enumerate(week) if s <= r["bucket"])
                days[idx] += r["seconds"]
        limit = None
        if devs:
            e = eng.effective_all()[devs[0]["mac"]]
            limit = e.limit_minutes
        locked_devs = [d for d in devs if d["locked"]]
        out_profiles.append({
            "id": p["id"], "name": p["name"], "kind": p["kind"], "color": p["color"], "tier": p["tier"],
            "monitoring": p["monitoring"],
            "devices": sorted(devs, key=lambda d: (not d["online"], d["name"].lower())),
            "online": any(d["online"] for d in devs),
            "used_seconds": round(sum(cats.values())),
            "limit_minutes": limit,
            "locked": bool(devs) and len(locked_devs) == len(devs),
            "lock_reason": locked_devs[0]["lock_reason"] if locked_devs else "",
            "lock_until": max((d["lock_until"] for d in locked_devs), default=0),
            "grant_until": max((d["grant_until"] for d in devs), default=0),
            "homework": any(d["homework"] for d in devs),
            "categories": [{"category": c, "label": CATEGORY_LABELS[c], "seconds": round(cats[c])}
                           for c in CATEGORIES if cats.get(c)],
            "top": [dict(site_info(eng, s), category=v[0], seconds=round(v[1]))
                    for s, v in sorted(sites.items(), key=lambda kv: -kv[1][1])[:4] if v[0] != "system"],
            "hours": [round(h) for h in hours],
            "week": [round(d) for d in days],
            "blocked": sum(r["b"] for r in blocked_rows if r["mac"] in macs),
            "alerts": sum(r["n"] for r in alert_rows if r["mac"] in macs),
        })
    unassigned = [d for d in devices if not d["profile_id"]]
    return {
        "now": time.time(), "day_start": start, "week_starts": week,
        "profiles": out_profiles,
        "unassigned": unassigned,
        "alerts": alerts(eng, limit=12),
        "unacked": db.val("SELECT COUNT(*) FROM alerts WHERE acked=0", (), 0),
    }


def alerts(eng: Engine, limit: int = 100, mac_in: list[str] | None = None, unacked_only: bool = False) -> list[dict[str, Any]]:
    where, args = ["1=1"], []
    if mac_in is not None:
        if not mac_in:
            return []
        where.append(f"a.mac IN ({_marks(len(mac_in))})")
        args += mac_in
    if unacked_only:
        where.append("a.acked=0")
    rows = eng.db.q(
        "SELECT a.*, d.name AS dname, d.hostname AS dhost, d.profile_id AS pid, p.name AS pname, p.color AS pcolor "
        "FROM alerts a LEFT JOIN devices d ON d.mac=a.mac LEFT JOIN profiles p ON p.id=d.profile_id "
        f"WHERE {' AND '.join(where)} ORDER BY a.ts DESC LIMIT ?", (*args, limit))
    return [{"id": r["id"], "key": r["key"], "ts": r["ts"], "mac": r["mac"], "kind": r["kind"],
             "severity": r["severity"], "title": r["title"], "detail": r["detail"], "acked": bool(r["acked"]),
             "device": r["dname"] or r["dhost"] or r["mac"], "profile_id": r["pid"], "profile": r["pname"],
             "color": r["pcolor"]} for r in rows]


def profile_activity(eng: Engine, pid: int, day: str | None = None) -> dict[str, Any]:
    """Everything the profile page draws for one local day, plus 14-day and 4-week context."""
    db, tz = eng.db, eng.cfg.tz
    devices = all_devices(eng)
    macs = _profile_macs(devices, pid)
    target = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=tz) if day else eng.now()
    start = eng.day_start(target)
    end = int((datetime.fromtimestamp(start, tz) + timedelta(hours=26)).replace(hour=0).timestamp())
    result: dict[str, Any] = {"day": datetime.fromtimestamp(start, tz).strftime("%Y-%m-%d"), "day_start": start,
                              "day_end": end, "macs": macs}
    if not macs:
        return {**result, "timeline": [], "categories": [], "sites": [], "trend": [], "heatmap": [], "blocked": [],
                "total_seconds": 0, "data_bytes": 0, "first_active": None, "last_active": None}
    m = _marks(len(macs))

    # 15-minute timeline by category
    slots: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    rows = db.q(f"SELECT bucket, category, SUM(seconds) AS s FROM activity WHERE mac IN ({m}) AND bucket>=? AND bucket<? "
                "AND seconds>0 GROUP BY bucket, category", (*macs, start, end))
    cats: dict[str, float] = defaultdict(float)
    first = last = None
    for r in rows:
        if r["category"] == "system":
            continue
        slot = (r["bucket"] - start) // 900
        slots[slot][r["category"]] += r["s"]
        cats[r["category"]] += r["s"]
        first = r["bucket"] if first is None else min(first, r["bucket"])
        last = r["bucket"] if last is None else max(last, r["bucket"])
    result["timeline"] = [{"slot": s, "t": start + s * 900, **{c: round(v) for c, v in sorted(cs.items())}}
                          for s, cs in sorted(slots.items())]
    result["categories"] = [{"category": c, "label": CATEGORY_LABELS[c], "seconds": round(cats[c])}
                            for c in CATEGORIES if cats.get(c)]
    result["total_seconds"] = round(sum(cats.values()))
    result["first_active"], result["last_active"] = first, (last + 300 if last else None)

    rows = db.q(f"SELECT site, category, SUM(seconds) AS s, SUM(queries) AS q, SUM(blocked) AS b FROM activity "
                f"WHERE mac IN ({m}) AND bucket>=? AND bucket<? AND category!='system' GROUP BY site "
                "HAVING s>0 OR q>0 ORDER BY s DESC, q DESC LIMIT 40", (*macs, start, end))
    result["sites"] = [dict(site_info(eng, r["site"]), category=r["category"], seconds=round(r["s"] or 0),
                            queries=r["q"], blocked=r["b"]) for r in rows]

    # 14-day trend by category
    days = local_midnights(tz, 14, datetime.fromtimestamp(start, tz))
    rows = db.q(f"SELECT bucket, category, seconds FROM activity WHERE mac IN ({m}) AND bucket>=? AND bucket<? "
                "AND seconds>0 AND category!='system'", (*macs, days[0], end))
    trend = [{"day_start": d, "date": datetime.fromtimestamp(d, tz).strftime("%Y-%m-%d"), "total": 0.0,
              **{c: 0.0 for c in CATEGORIES}} for d in days]
    import bisect
    for r in rows:
        i = bisect.bisect_right(days, r["bucket"]) - 1
        trend[i][r["category"]] = trend[i].get(r["category"], 0) + r["seconds"]
        trend[i]["total"] += r["seconds"]
    result["trend"] = [{k: (round(v) if isinstance(v, float) else v) for k, v in t.items()} for t in trend]

    # 4-week weekday x hour heatmap (average seconds per hour slot)
    weeks = 4
    hm_start = local_midnights(tz, weeks * 7, datetime.fromtimestamp(start, tz))[0]
    rows = db.q(f"SELECT bucket, SUM(seconds) AS s FROM activity WHERE mac IN ({m}) AND bucket>=? AND bucket<? "
                "AND seconds>0 AND category!='system' GROUP BY bucket", (*macs, hm_start, end))
    grid = [[0.0] * 24 for _ in range(7)]
    for r in rows:
        dt = datetime.fromtimestamp(r["bucket"], tz)
        grid[dt.weekday()][dt.hour] += r["s"]
    result["heatmap"] = [[round(v / weeks) for v in row] for row in grid]

    rows = db.q(f"SELECT bucket, site, reason, SUM(blocked) AS b, mac FROM activity WHERE mac IN ({m}) AND bucket>=? "
                f"AND bucket<? AND blocked>0 AND reason IN ({_marks(len(NOTABLE_REASONS))}) "
                "GROUP BY bucket, site ORDER BY bucket DESC LIMIT 200", (*macs, start, end, *NOTABLE_REASONS))
    result["blocked"] = [dict(site_info(eng, r["site"]), ts=r["bucket"], reason=r["reason"], count=r["b"],
                              mac=r["mac"]) for r in rows]
    result["data_bytes"] = db.val(f"SELECT SUM(up+down) FROM usage WHERE mac IN ({m}) AND minute>=? AND minute<?",
                                  (*macs, start, end), 0)
    return result


def events(eng: Engine, macs: list[str], kind: str, limit: int = 100, before: float | None = None,
           day_start: int | None = None, day_end: int | None = None) -> list[dict[str, Any]]:
    if not macs:
        return []
    where = [f"mac IN ({_marks(len(macs))})", "kind=?"]
    args: list[Any] = [*macs, kind]
    if before:
        where.append("ts<?")
        args.append(before)
    if day_start is not None:
        where.append("ts>=? AND ts<?")
        args += [day_start, day_end]
    rows = eng.db.q(f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ?", (*args, limit))
    return [{"id": r["id"], "ts": r["ts"], "mac": r["mac"], "host": r["host"], **json.loads(r["data"])} for r in rows]


def system(eng: Engine) -> dict[str, Any]:
    lists = []
    for tier, inst in eng.instances.items():
        try:
            for f in inst.filter_status():
                lists.append({"tier": tier, "name": f["name"], "rules": f.get("rules_count", 0),
                              "updated": f.get("last_updated", ""), "enabled": f.get("enabled", False)})
        except Exception:
            pass
    db = eng.db
    return {
        "nodes": eng.nodes(), "lists": lists, "role": eng.cfg.role, "node_id": eng.cfg.node_id,
        "counts": {"facts": db.val("SELECT COUNT(*) FROM facts", (), 0),
                   "events": db.val("SELECT COUNT(*) FROM events", (), 0),
                   "activity": db.val("SELECT COUNT(*) FROM activity", (), 0)},
        "pinned": [{"host": r["host"], "mac": r["mac"], "n": r["n"], "ts": r["ts"]} for r in db.q(
            "SELECT host, mac, COUNT(*) AS n, MAX(ts) AS ts FROM events WHERE kind='pinned' AND ts>? "
            "GROUP BY host, mac ORDER BY ts DESC LIMIT 50", (time.time() - 86400,))],
        "network": {"lan": eng.cfg.lan_cidr, "router": eng.cfg.router_ip, "vip": eng.cfg.vip or eng.cfg.node_ip},
    }
