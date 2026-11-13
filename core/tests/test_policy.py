from datetime import datetime
from zoneinfo import ZoneInfo

from breakwater import policy

TZ = ZoneInfo("America/Vancouver")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    # November 2026: the 9th is a Monday.
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


def kid(**pol):
    return {"id": 1, "kind": "kid", "tier": "strict", "monitoring": "full", "policy": pol}


def dev(**kw):
    return {"mac": "aa:bb:cc:00:00:01", "inspect": 1, "route": None, "overrides": {}, **kw}


def test_bedtime_spans_midnight():
    p = kid()  # default: school nights 21:00-07:00, Fri/Sat 22:00-08:00
    assert not policy.evaluate(dev(), p, at(9, 20, 59)).locked
    e = policy.evaluate(dev(), p, at(9, 21, 0))
    assert e.locked and e.lock_reason == "bedtime"
    assert datetime.fromtimestamp(e.lock_until, TZ) == at(10, 7)
    assert policy.evaluate(dev(), p, at(10, 6, 59)).lock_reason == "bedtime"  # Monday's window, Tuesday morning
    assert not policy.evaluate(dev(), p, at(10, 7, 0)).locked


def test_weekend_bedtime_is_later():
    p = kid()
    assert not policy.evaluate(dev(), p, at(13, 21, 30)).locked  # Friday
    assert policy.evaluate(dev(), p, at(13, 22, 5)).locked
    assert policy.evaluate(dev(), p, at(14, 7, 30)).locked  # Saturday morning, Friday's window runs to 08:00


def test_daily_limit_and_bonus():
    p = kid(daily_limit={"enabled": True, "minutes": {d: 60 for d in policy.DAYS}})
    assert not policy.evaluate(dev(), p, at(9, 16), used_seconds=3599).locked
    e = policy.evaluate(dev(), p, at(9, 16), used_seconds=3600)
    assert e.locked and e.lock_reason == "limit"
    assert datetime.fromtimestamp(e.lock_until, TZ) == at(10, 0)
    bonus = at(9, 16, 30).timestamp()
    e = policy.evaluate(dev(), p, at(9, 16), used_seconds=9999, grant_until=bonus)
    assert not e.locked and e.grant_until == bonus
    assert policy.evaluate(dev(), p, at(9, 16, 31), used_seconds=9999, grant_until=bonus).locked


def test_bonus_lifts_bedtime_but_not_a_manual_lock():
    p = kid()
    grant = at(9, 21, 30).timestamp()
    assert not policy.evaluate(dev(), p, at(9, 21, 10), grant_until=grant).locked
    manual = {"locked": 1, "until": 0}
    e = policy.evaluate(dev(), p, at(9, 21, 10), grant_until=grant, device_lock=manual)
    assert e.locked and e.lock_reason == "manual"
    e = policy.evaluate(dev(), p, at(9, 12), profile_lock=manual)
    assert e.locked and e.lock_reason == "manual"


def test_timed_manual_lock_expires():
    p = kid(bedtime={"enabled": False, "days": {}})
    lock = {"locked": 1, "until": at(9, 13).timestamp()}
    assert policy.evaluate(dev(), p, at(9, 12, 59), device_lock=lock).locked
    assert not policy.evaluate(dev(), p, at(9, 13, 0), device_lock=lock).locked
    assert not policy.evaluate(dev(), p, at(9, 12), device_lock={"locked": 0, "until": 0}).locked


def test_homework_adds_services_only_inside_window():
    hw = {"enabled": True, "services": ["group:gaming", "youtube"], "windows": {"mon": [["16:00", "18:00"]]}}
    p = kid(blocked_services=["tiktok"], homework=hw)
    groups = {"gaming": ["roblox", "steam"]}
    assert policy.evaluate(dev(), p, at(9, 15, 59), groups=groups).blocked_services == ["tiktok"]
    e = policy.evaluate(dev(), p, at(9, 16, 0), groups=groups)
    assert e.homework and e.blocked_services == ["roblox", "steam", "tiktok", "youtube"]
    assert not policy.evaluate(dev(), p, at(10, 16, 30), groups=groups).homework  # Tuesday has no window


def test_device_override_wins_over_profile():
    p = kid(youtube="strict", keywords=["Fortnite "])
    e = policy.evaluate(dev(overrides={"youtube": "off"}), p, at(9, 12))
    assert e.youtube == "off" and e.keywords == ["fortnite"]


def test_inspection_needs_full_monitoring_and_gateway_route():
    assert policy.evaluate(dev(), kid(), at(9, 12)).inspect
    sites = {**kid(), "monitoring": "sites"}
    assert not policy.evaluate(dev(), sites, at(9, 12)).inspect
    assert not policy.evaluate(dev(route="router"), kid(), at(9, 12)).inspect
    assert not policy.evaluate(dev(inspect=0), kid(), at(9, 12)).inspect


def test_adult_defaults_and_self_restriction():
    adult = {"id": 2, "kind": "adult", "tier": "basic", "monitoring": "off",
             "policy": {"block_domains": ["reddit.com"], "blocked_services": ["twitter"]}}
    e = policy.evaluate(dev(), adult, at(9, 23))
    assert (e.route, e.inspect, e.locked, e.monitoring) == ("router", False, False, "off")
    assert e.block_domains == ["reddit.com"] and e.blocked_services == ["twitter"]
    none = {**adult, "tier": "none"}
    e = policy.evaluate(dev(), none, at(9, 23))
    assert e.blocked_services == [] and e.block_domains == [] and not e.safesearch


def test_unknown_devices_follow_the_quarantine_setting():
    q = policy.evaluate(dev(), None, at(9, 12), quarantine_unknown=True, groups={"dating": ["tinder"]})
    assert (q.tier, q.route, q.known) == ("strict", "gateway", False) and "tinder" in q.blocked_services
    g = policy.evaluate(dev(), None, at(9, 12), quarantine_unknown=False)
    assert (g.tier, g.route) == ("basic", "router")
    assert policy.evaluate(dev(), None, at(9, 23), device_lock={"locked": 1, "until": 0}).locked
    assert not policy.evaluate(dev(), None, at(9, 23)).locked  # no bedtime for unclassified devices


def test_lock_until_morning_uses_bedtime_end():
    pol = policy.default_policy("kid")
    assert policy.next_bedtime_end(pol, at(9, 19)) == at(10, 7)   # before bedtime: tonight's end
    assert policy.next_bedtime_end(pol, at(9, 23)) == at(10, 7)   # during bedtime
    assert policy.next_bedtime_end(policy.default_policy("adult"), at(9, 19)) == at(10, 7)  # fallback 07:00
