"""Pure policy evaluation: given a device, its profile and the clock, what applies right now?

No I/O in this module, so every rule here is unit-tested directly.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dtime, timedelta
from typing import Any

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TIERS = ("strict", "basic", "none")
MONITORING = ("full", "sites", "off")

_SCHOOL_NIGHT = ["21:00", "07:00"]
_WEEKEND = ["22:00", "08:00"]

KID_POLICY: dict[str, Any] = {
    "safesearch": True,
    "youtube": "moderate",  # off | moderate | strict
    "blocked_services": ["group:dating", "group:gambling", "onlyfans", "4chan"],
    "homework": {"enabled": False, "services": ["group:gaming", "group:streaming", "group:social_network"],
                 "windows": {d: [] for d in DAYS}},
    "bedtime": {"enabled": True,
                "days": {"sun": _SCHOOL_NIGHT, "mon": _SCHOOL_NIGHT, "tue": _SCHOOL_NIGHT, "wed": _SCHOOL_NIGHT,
                         "thu": _SCHOOL_NIGHT, "fri": _WEEKEND, "sat": _WEEKEND}},
    "daily_limit": {"enabled": False, "minutes": {d: 120 for d in DAYS[:5]} | {"sat": 180, "sun": 180}},
    "allow_domains": [],
    "block_domains": [],
    "keywords": [],
}

ADULT_POLICY: dict[str, Any] = {
    "safesearch": False,
    "youtube": "off",
    "blocked_services": [],
    "homework": {"enabled": False, "services": [], "windows": {d: [] for d in DAYS}},
    "bedtime": {"enabled": False, "days": {d: None for d in DAYS}},
    "daily_limit": {"enabled": False, "minutes": {d: None for d in DAYS}},
    "allow_domains": [],
    "block_domains": [],
    "keywords": [],
}


def default_policy(kind: str) -> dict[str, Any]:
    return copy.deepcopy(KID_POLICY if kind == "kid" else ADULT_POLICY)


def merged_policy(profile: dict[str, Any] | None, device: dict[str, Any] | None) -> dict[str, Any]:
    """Profile policy with the device's top-level overrides laid on top."""
    kind = (profile or {}).get("kind", "kid")
    pol = default_policy(kind)
    pol.update((profile or {}).get("policy") or {})
    pol.update((device or {}).get("overrides") or {})
    return pol


def _hm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h) % 24, int(m))


def _window_end(now: datetime, day_offset: int, start: str, end: str) -> tuple[datetime, datetime]:
    """Concrete [start, end) for a window that begins on (today + day_offset)."""
    base = (now + timedelta(days=day_offset)).date()
    s = datetime.combine(base, _hm(start), tzinfo=now.tzinfo)
    e = datetime.combine(base, _hm(end), tzinfo=now.tzinfo)
    if e <= s:  # runs past midnight
        e += timedelta(days=1)
    return s, e


def bedtime_until(policy: dict[str, Any], now: datetime) -> datetime | None:
    """If `now` is inside a bedtime window, return when it ends."""
    bt = policy.get("bedtime") or {}
    if not bt.get("enabled"):
        return None
    days = bt.get("days") or {}
    for offset in (0, -1):  # tonight's window, or last night's still running
        key = DAYS[(now + timedelta(days=offset)).weekday()]
        win = days.get(key)
        if not win:
            continue
        s, e = _window_end(now, offset, win[0], win[1])
        if s <= now < e:
            return e
    return None


def next_bedtime_end(policy: dict[str, Any], now: datetime) -> datetime:
    """When 'lock until morning' should lift: the end of tonight's bedtime, else 07:00."""
    bt = policy.get("bedtime") or {}
    days = bt.get("days") or {}
    current = bedtime_until(policy, now)
    if current:
        return current
    win = days.get(DAYS[now.weekday()]) if bt.get("enabled") else None
    if win:
        _, e = _window_end(now, 0, win[0], win[1])
        if e > now:
            return e
    morning = datetime.combine(now.date(), dtime(7, 0), tzinfo=now.tzinfo)
    return morning if morning > now else morning + timedelta(days=1)


def homework_active(policy: dict[str, Any], now: datetime) -> bool:
    hw = policy.get("homework") or {}
    if not hw.get("enabled"):
        return False
    for offset in (0, -1):
        key = DAYS[(now + timedelta(days=offset)).weekday()]
        for win in (hw.get("windows") or {}).get(key) or []:
            s, e = _window_end(now, offset, win[0], win[1])
            if s <= now < e:
                return True
    return False


def limit_minutes(policy: dict[str, Any], now: datetime) -> int | None:
    dl = policy.get("daily_limit") or {}
    if not dl.get("enabled"):
        return None
    v = (dl.get("minutes") or {}).get(DAYS[now.weekday()])
    return int(v) if v is not None else None


def expand_services(ids: list[str], groups: dict[str, list[str]]) -> list[str]:
    out: set[str] = set()
    for i in ids or []:
        if i.startswith("group:"):
            out.update(groups.get(i[6:], []))
        else:
            out.add(i)
    return sorted(out)


def _lock_active(lock: dict[str, Any] | None, now_ts: float) -> tuple[bool, float]:
    if not lock or not lock.get("locked"):
        return False, 0
    until = float(lock.get("until") or 0)
    if until and until <= now_ts:
        return False, 0
    return True, until


@dataclass
class Effective:
    tier: str = "strict"
    monitoring: str = "sites"
    route: str = "gateway"  # gateway | router
    inspect: bool = False
    locked: bool = False
    lock_reason: str = ""  # manual | bedtime | limit
    lock_until: float = 0  # epoch seconds, 0 = until unlocked
    grant_until: float = 0
    homework: bool = False
    safesearch: bool = True
    youtube: str = "moderate"
    blocked_services: list[str] = field(default_factory=list)
    allow_domains: list[str] = field(default_factory=list)
    block_domains: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    limit_minutes: int | None = None
    known: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate(
    device: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    now: datetime,
    used_seconds: float = 0,
    device_lock: dict[str, Any] | None = None,
    profile_lock: dict[str, Any] | None = None,
    grant_until: float = 0,
    quarantine_unknown: bool = True,
    groups: dict[str, list[str]] | None = None,
) -> Effective:
    """Resolve everything that applies to one device at `now` (timezone-aware, local)."""
    groups = groups or {}
    now_ts = now.timestamp()
    eff = Effective()

    if profile is None:
        # Unclassified device: either held to the kids' baseline or treated as a plain guest.
        eff.tier = "strict" if quarantine_unknown else "basic"
        eff.route = "gateway" if quarantine_unknown else "router"
        eff.monitoring = "sites"
        eff.blocked_services = expand_services(KID_POLICY["blocked_services"], groups) if quarantine_unknown else []
        eff.safesearch = quarantine_unknown
        eff.youtube = "moderate" if quarantine_unknown else "off"
        active, until = _lock_active(device_lock, now_ts)
        if active:
            eff.locked, eff.lock_reason, eff.lock_until = True, "manual", until
        return eff

    pol = merged_policy(profile, device)
    eff.known = True
    eff.tier = profile.get("tier", "strict")
    eff.monitoring = profile.get("monitoring", "full")
    dev_route = (device or {}).get("route")
    eff.route = dev_route or ("gateway" if profile.get("kind") == "kid" else "router")
    eff.inspect = bool((device or {}).get("inspect")) and eff.monitoring == "full" and eff.route == "gateway"

    if eff.tier != "none":
        eff.safesearch = bool(pol.get("safesearch"))
        eff.youtube = pol.get("youtube") or "off"
        services = list(pol.get("blocked_services") or [])
        eff.homework = homework_active(pol, now)
        if eff.homework:
            services += (pol.get("homework") or {}).get("services") or []
        eff.blocked_services = expand_services(services, groups)
        eff.allow_domains = sorted(set(pol.get("allow_domains") or []))
        eff.block_domains = sorted(set(pol.get("block_domains") or []))
    else:
        eff.safesearch, eff.youtube = False, "off"
    eff.keywords = sorted({k.strip().lower() for k in pol.get("keywords") or [] if k.strip()})
    eff.limit_minutes = limit_minutes(pol, now)

    # Locks, strongest first: a manual lock always wins; a bonus grant lifts schedule locks.
    for lock in (device_lock, profile_lock):
        active, until = _lock_active(lock, now_ts)
        if active:
            eff.locked, eff.lock_reason, eff.lock_until = True, "manual", until
            return eff
    if grant_until and grant_until > now_ts:
        eff.grant_until = grant_until
        return eff
    bed = bedtime_until(pol, now)
    if bed:
        eff.locked, eff.lock_reason, eff.lock_until = True, "bedtime", bed.timestamp()
        return eff
    if eff.limit_minutes is not None and used_seconds >= eff.limit_minutes * 60:
        midnight = datetime.combine(now.date() + timedelta(days=1), dtime(0, 0), tzinfo=now.tzinfo)
        eff.locked, eff.lock_reason, eff.lock_until = True, "limit", midnight.timestamp()
    return eff
