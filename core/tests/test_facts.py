import time

import pytest

from breakwater import alerts
from breakwater.adguard import bootstrap_yaml, parse_time
from breakwater.categories import catalogue
from breakwater.db import DB
from breakwater.facts import BUCKET, Facts

MAC = "aa:bb:cc:00:00:01"
T0 = 1_800_000_000 - 1_800_000_000 % BUCKET


@pytest.fixture
def facts():
    return Facts(DB(":memory:"), "n1", catalogue())


def seconds(f: Facts, bucket: int = T0) -> dict[str, float]:
    return {r["site"]: r["seconds"] for r in f.db.q("SELECT site, seconds FROM activity WHERE bucket=? AND seconds>0", (bucket,))}


def test_classify_known_apps_and_background():
    cat = catalogue()
    assert cat.classify("rr3---sn-abc.googlevideo.com") == ("@youtube", "streaming")
    assert cat.classify("www.roblox.com") == ("@roblox", "games")
    assert cat.classify("docs.google.com") == ("docs.google.com", "learning")
    assert cat.classify("fonts.gstatic.com")[1] == "system"
    assert cat.classify("some.blog.example.co.uk") == ("example.co.uk", "other")
    assert cat.classify("printer.local")[1] == "system"


def test_time_is_split_by_lookups_and_ignores_background(facts):
    facts.append("dns", {"rows": [[MAC, T0, "@youtube", "streaming", 9, 0, ""], [MAC, T0, "example.com", "other", 3, 0, ""],
                                  [MAC, T0, "gstatic.com", "system", 50, 0, ""]]})
    # three of the five minutes carry real traffic
    facts.append("usage", {"rows": [[MAC, T0 + m * 60, 10_000, 500_000 if m < 3 else 2_000] for m in range(5)]})
    facts.recompute_dirty()
    assert seconds(facts) == {"@youtube": 135.0, "example.com": 45.0}


def test_idle_chatter_counts_for_nothing(facts):
    facts.append("dns", {"rows": [[MAC, T0, "@youtube", "streaming", 4, 0, ""]]})
    facts.append("usage", {"rows": [[MAC, T0 + m * 60, 500, 900] for m in range(5)]})
    facts.recompute_dirty()
    assert seconds(facts) == {}


def test_long_video_carries_forward_without_new_lookups(facts):
    facts.append("dns", {"rows": [[MAC, T0, "@youtube", "streaming", 6, 0, ""]]})
    facts.append("usage", {"rows": [[MAC, T0 + m * 60, 0, 4_000_000] for m in range(15)]})
    while facts.recompute_dirty():
        pass
    assert seconds(facts, T0 + BUCKET) == {"@youtube": 300.0}
    assert seconds(facts, T0 + 2 * BUCKET) == {"@youtube": 300.0}
    # a big download with nothing in use before it is not screen time
    facts.append("usage", {"rows": [[MAC, T0 + 7200, 0, 90_000_000]]})
    facts.recompute_dirty()
    assert seconds(facts, T0 + 7200) == {}


def test_unmetered_devices_fall_back_to_lookup_estimate(facts):
    facts.append("dns", {"rows": [[MAC, T0, "@reddit", "social", 4, 0, ""]]})
    facts.recompute_dirty()
    assert seconds(facts) == {"@reddit": 120.0}


def test_blocked_adult_attempts_alert_once_per_hour(facts):
    for i in range(2):
        facts.append("dns", {"rows": [[MAC, T0 + i * BUCKET, "badsite.com", "other", 1, 1, "adult"]]}, ts=T0)
    assert facts.db.val("SELECT COUNT(*) FROM alerts") is None or facts.db.val("SELECT COUNT(*) FROM alerts") == 0
    facts.append("dns", {"rows": [[MAC, T0 + 2 * BUCKET, "badsite.com", "other", 2, 2, "adult"]]}, ts=T0)
    facts.append("dns", {"rows": [[MAC, T0 + 3 * BUCKET, "badsite.com", "other", 5, 5, "adult"]]}, ts=T0)
    rows = facts.db.q("SELECT kind, severity FROM alerts")
    assert [(r["kind"], r["severity"]) for r in rows] == [("blocked_adult", "high")]
    # ad blocking never raises an alert
    facts.append("dns", {"rows": [[MAC, T0, "doubleclick.net", "system", 99, 99, "ads"]]}, ts=T0)
    assert facts.db.val("SELECT COUNT(*) FROM alerts") == 1


def test_flagged_search_and_keyword_block_alert(facts):
    facts.append("web", {"items": [
        {"t": T0, "mac": MAC, "k": "search", "host": "www.google.com", "engine": "Google", "q": "how to bypass  Family Link"},
        {"t": T0, "mac": MAC, "k": "search", "host": "www.google.com", "engine": "Google", "q": "minecraft skins"},
        {"t": T0, "mac": MAC, "k": "block", "host": "example.com", "keyword": "fortnite"},
        {"t": T0, "mac": MAC, "k": "video", "host": "www.youtube.com", "vid": "abc", "title": "x"}]})
    kinds = sorted(r["kind"] for r in facts.db.q("SELECT kind FROM alerts"))
    assert kinds == ["keyword_block", "search_bypass"]
    assert facts.db.val("SELECT COUNT(*) FROM events") == 4


def test_watch_terms_respect_word_boundaries():
    assert alerts.check("i want to die")[0] == "self_harm"
    assert alerts.check("unicorn popcorn recipe") is None       # 'porn' inside another word
    assert alerts.check("essex county map") is None             # 'sex' inside another word
    assert alerts.check("Pokemon cards", ["pokemon"]) == ("custom", "medium", "pokemon")


def test_locks_are_last_write_wins_across_nodes(facts):
    a, b = facts, Facts(DB(":memory:"), "n2", catalogue())
    a.append("action", {"type": "lock", "target": f"d:{MAC}", "until": 0}, ts=100)
    b.append("action", {"type": "unlock", "target": f"d:{MAC}"}, ts=200)
    a.ingest(b.since(a.vector()))
    b.ingest(a.since(b.vector()))
    for node in (a, b):
        assert node.db.val("SELECT locked FROM locks WHERE target=?", (f"d:{MAC}",)) == 0
    assert a.vector() == b.vector() == {"n1": 1, "n2": 1}


def test_replication_is_idempotent_and_converges(facts):
    a, b = facts, Facts(DB(":memory:"), "n2", catalogue())
    a.append("dns", {"rows": [[MAC, T0, "@youtube", "streaming", 5, 0, ""]]})
    b.append("dns", {"rows": [[MAC, T0, "@youtube", "streaming", 7, 1, "service"]]})
    batch = a.since({})
    assert b.ingest(batch) == 1 and b.ingest(batch) == 0  # second delivery changes nothing
    a.ingest(b.since(a.vector()))
    for node in (a, b):
        r = node.db.one("SELECT queries, blocked FROM activity WHERE site='@youtube'")
        assert (r["queries"], r["blocked"]) == (12, 1)


def test_new_device_is_registered_on_primary_only():
    primary = Facts(DB(":memory:"), "n1", catalogue(), is_primary=True)
    standby = Facts(DB(":memory:"), "n2", catalogue(), is_primary=False)
    lease = {"mac": "AA:BB:CC:00:00:09", "ip": "192.168.0.77", "hostname": "pixel"}
    primary.append("lease", lease)
    standby.ingest(primary.since({}))
    assert primary.db.val("SELECT ip FROM devices WHERE mac='aa:bb:cc:00:00:09'") == "192.168.0.77"
    assert standby.db.val("SELECT COUNT(*) FROM devices") == 0  # arrives with the config snapshot instead
    assert standby.db.val("SELECT ip FROM leases WHERE mac='aa:bb:cc:00:00:09'") == "192.168.0.77"


def test_adguard_helpers():
    assert parse_time("2026-11-12T20:00:00.123456789Z") == pytest.approx(parse_time("2026-11-12T12:00:00.123456-08:00"))
    assert parse_time("2026-11-12T20:00:00Z") < parse_time("2026-11-12T20:00:00.5Z")
    y = bootstrap_yaml("strict", 3001, 5301, "bw", "a-long-password")
    assert "family.cloudflare-dns.com" in y and "port: 5301" in y and "a-long-password" not in y
    assert "nsfw" not in bootstrap_yaml("basic", 3002, 5302, "bw", "a-long-password")


def test_prune_keeps_latest_fact_per_origin(facts):
    facts.append("action", {"type": "lock", "target": "d:x", "until": 0}, ts=time.time() - 40 * 86400)
    facts.append("action", {"type": "unlock", "target": "d:x"}, ts=time.time() - 39 * 86400)
    facts.prune()
    assert facts.vector() == {"n1": 2}  # the cursor survives so peers never re-send from zero
