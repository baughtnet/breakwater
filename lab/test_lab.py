"""End-to-end tests against the simulated house in docker-compose.yml.

    docker compose up -d --build        # from this directory
    ../.venv/bin/python -m pytest -x -q test_lab.py

They drive the real stack (AdGuard, nftables, dnsmasq, keepalived, mitmproxy, core) from the fake
devices' point of view. They need internet access (blocklists and a few public sites) and run in
file order; the failover test goes last because it stops a node.
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

NODE1, NODE2 = "http://127.0.0.1:8081", "http://127.0.0.1:8082"
VIP = "10.77.0.5"
USER = {"username": "parent", "password": "lab-parent-password"}
MAC = {"kid-pc": "02:42:0a:4d:10:01", "kid-console": "02:42:0a:4d:10:02", "adult": "02:42:0a:4d:20:01",
       "guest": "02:42:0a:4d:30:01"}
NAV = '-H "Sec-Fetch-Dest: document" -H "Sec-Fetch-Mode: navigate"'
ADULT_SITE = "pornhub.com"


def dx(service: str, cmd: str, timeout: int = 40) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", "exec", "-T", service, "sh", "-c", cmd],
                          capture_output=True, text=True, timeout=timeout)


def out(service: str, cmd: str, timeout: int = 40) -> str:
    return dx(service, cmd, timeout).stdout.strip()


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], check=True, capture_output=True, timeout=180)


def wait_for(fn, timeout: float = 30, every: float = 1.0, what: str = "condition"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except Exception as exc:  # services restarting, etc.
            last = exc
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what}; last result: {last!r}")


def resolve(service: str, name: str, server: str = "") -> list[str]:
    at = f"@{server}" if server else ""
    return out(service, f"dig +short +time=3 +tries=1 {at} {name} A").splitlines()


def is_blocked(answers: list[str]) -> bool:
    return answers == [] or set(answers) <= {"0.0.0.0"}


def fetch_code(service: str, url: str, extra: str = "") -> str:
    """HTTP status as a string, or '000' if the connection failed."""
    return out(service, f'curl -s -o /dev/null -m 6 -w "%{{http_code}}" {extra} "{url}"') or "000"


@pytest.fixture(scope="session")
def api() -> httpx.Client:
    c = httpx.Client(base_url=NODE1, timeout=15)
    wait_for(lambda: c.get("/healthz").status_code == 200, 90, what="core on node1")
    if c.get("/api/session").json()["setup_needed"]:
        assert c.post("/api/setup", json=USER).status_code == 200
    else:
        assert c.post("/api/login", json=USER).status_code == 200
    return c


@pytest.fixture(scope="session")
def house(api: httpx.Client) -> dict:
    """Two profiles, devices assigned, everything settled."""
    devices = wait_for(lambda: (d := api.get("/api/devices").json()) and len(d) >= 4 and d, 90, what="devices via DHCP")
    by_mac = {d["mac"]: d for d in devices}
    profiles = {p["name"]: p for p in api.get("/api/profiles").json()}
    no_schedule = {"bedtime": {"enabled": False, "days": {}}, "daily_limit": {"enabled": False, "minutes": {}}}
    if "Kid" not in profiles:
        api.post("/api/profiles", json={"name": "Kid", "kind": "kid", "policy": no_schedule}).raise_for_status()
        api.post("/api/profiles", json={"name": "Grownup", "kind": "adult"}).raise_for_status()
        profiles = {p["name"]: p for p in api.get("/api/profiles").json()}
    kid, adult = profiles["Kid"]["id"], profiles["Grownup"]["id"]
    api.put(f"/api/profiles/{kid}", json={"policy": {**no_schedule, "keywords": ["zebrablock"]}}).raise_for_status()
    api.put(f"/api/devices/{by_mac[MAC['kid-pc']]['id']}", json={"profile_id": kid, "inspect": True, "name": "Kid PC"})
    api.put(f"/api/devices/{by_mac[MAC['kid-console']]['id']}", json={"profile_id": kid, "inspect": False, "name": "Console"})
    api.put(f"/api/devices/{by_mac[MAC['adult']]['id']}", json={"profile_id": adult, "name": "Adult phone"})
    for target in ("kid-pc", "kid-console"):
        api.post("/api/unlock", json={"target": "device", "id": by_mac[MAC[target]]["id"]})
    api.post("/api/unlock", json={"target": "profile", "id": kid})
    api.post("/api/bonus", json={"profile_id": kid, "minutes": 0})

    def lists_ready():
        lists = api.get("/api/system").json()["lists"]
        strict = [x for x in lists if x["tier"] == "strict"]
        return len(strict) >= 5 and all(x["rules"] > 0 for x in strict)
    wait_for(lists_ready, 120, every=5, what="blocklists to download")
    # the adult's lease must renew to pick up "gateway = router"
    dx("adult", "kill -USR2 $(pidof udhcpc); sleep 1; kill -USR1 $(pidof udhcpc)")
    wait_for(lambda: "via 10.77.0.1" in out("adult", "ip route show default"), 30, what="adult routed via the router")
    wait_for(lambda: is_blocked(resolve("kid-pc", ADULT_SITE)), 60, what="strict filter live for kid-pc")
    ids = {name: by_mac[mac]["id"] for name, mac in MAC.items()}
    return {"kid": kid, "adult": adult, "ids": ids}


# --------------------------------------------------------------------------- DHCP and routing
def test_devices_get_addresses_and_routes(house):
    for dev in ("kid-pc", "kid-console", "guest"):
        assert f"via {VIP}" in out(dev, "ip route show default"), dev  # kids and unknown devices go through Breakwater
        assert VIP in out(dev, "cat /etc/resolv.conf")
    assert "via 10.77.0.1" in out("adult", "ip route show default")  # adults keep the router as gateway
    assert VIP in out("adult", "cat /etc/resolv.conf")  # ...but still use Breakwater for DNS


# --------------------------------------------------------------------------- DNS filtering
def test_kid_dns_blocks_adult_content_and_forces_safesearch(house):
    assert is_blocked(resolve("kid-pc", ADULT_SITE))
    assert is_blocked(resolve("kid-console", ADULT_SITE))
    assert not is_blocked(resolve("kid-pc", "example.com"))
    assert any("forcesafesearch.google.com" in a for a in resolve("kid-pc", "www.google.com"))
    assert any("restrict" in a for a in resolve("kid-pc", "www.youtube.com"))
    assert is_blocked(resolve("kid-pc", "tinder.com"))  # dating apps are blocked for kids by default


def test_hard_coded_dns_is_hijacked(house):
    """A device that insists on 8.8.8.8 still gets Breakwater's answer."""
    assert is_blocked(resolve("kid-pc", ADULT_SITE, server="8.8.8.8"))
    assert any("forcesafesearch" in a for a in resolve("kid-console", "www.google.com", server="9.9.9.9"))


def test_filter_ports_cannot_be_reached_directly(house):
    """Pointing straight at the lenient filter's port must not work."""
    for port in (5301, 5302):
        answer = out("kid-pc", f"dig +time=2 +tries=1 @{VIP} -p {port} example.com A 2>&1")
        assert "timed out" in answer and "ANSWER SECTION" not in answer, answer


def test_unknown_device_is_quarantined(house):
    assert is_blocked(resolve("guest", ADULT_SITE))


def test_adult_basic_tier_blocks_ads_only(house):
    assert is_blocked(resolve("adult", "googleads.g.doubleclick.net"))
    assert not is_blocked(resolve("adult", ADULT_SITE))
    assert not any("forcesafesearch" in a for a in resolve("adult", "www.google.com"))


# --------------------------------------------------------------------------- bypass prevention
def test_encrypted_dns_and_vpn_ports_are_closed_for_kids(house):
    assert dx("kid-pc", "nc -z -w 3 1.1.1.1 853").returncode != 0      # DNS-over-TLS
    assert fetch_code("kid-console", "https://1.1.1.1/") == "000"       # DNS-over-HTTPS by address
    assert dx("kid-console", "nc -z -w 3 example.com 1194").returncode != 0
    assert fetch_code("kid-console", "https://example.com/") == "200"   # normal browsing unaffected
    assert dx("adult", "nc -z -w 3 1.1.1.1 853").returncode == 0        # adults are not restricted


def test_router_only_accepts_kid_traffic_via_breakwater(house):
    """With the kid's MAC blocked on the router, a hand-set gateway gets nowhere."""
    try:
        dx("kid-console", "ip route replace default via 10.77.0.1")
        assert fetch_code("kid-console", "https://example.com/") == "000"
    finally:
        dx("kid-console", f"ip route replace default via {VIP}")
    assert fetch_code("kid-console", "https://example.com/") == "200"


# --------------------------------------------------------------------------- HTTPS inspection
def test_dashboard_refuses_kid_devices_but_serves_the_certificate(house):
    assert fetch_code("kid-pc", f"http://{VIP}:8080/api/session") == "403"
    assert fetch_code("kid-pc", f"http://{VIP}:8080/ca.pem") == "200"
    assert fetch_code("adult", f"http://{VIP}:8080/api/session") == "200"


def test_inspected_device_is_decrypted_and_logged(house, api):
    dx("kid-pc", "[ -f /tmp/orig-bundle.crt ] || cp /etc/ssl/certs/ca-certificates.crt /tmp/orig-bundle.crt")
    dx("kid-pc", f"curl -s http://{VIP}:8080/ca.pem -o /usr/local/share/ca-certificates/breakwater.crt && update-ca-certificates")
    issuer = wait_for(lambda: out("kid-pc", "curl -sv -m 8 https://example.com/ -o /dev/null 2>&1 | grep -i 'issuer:'"),
                      30, what="proxy to answer")
    assert "Breakwater" in issuer
    marker = f"bwlab{int(time.time())}"
    assert fetch_code("kid-pc", f"https://example.com/?page={marker}", NAV) == "200"
    assert fetch_code("kid-pc", f"https://www.google.com/search?q={marker}+volcano+facts", NAV) in ("200", "429", "302")
    assert fetch_code("kid-pc", f"https://duckduckgo.com/?q=free+vpn+{marker}", NAV) in ("200", "202", "301", "302")

    def logged():
        visits = api.get(f"/api/profiles/{house['kid']}/events", params={"kind": "visit"}).json()
        searches = api.get(f"/api/profiles/{house['kid']}/events", params={"kind": "search"}).json()
        v = [x for x in visits if marker in x.get("url", "")]
        s = [x for x in searches if marker in x.get("q", "")]
        return (v, s) if v and len(s) >= 2 else None
    visits, searches = wait_for(logged, 20, what="visit and searches to reach the dashboard")
    assert visits[0]["title"] == "Example Domain" and visits[0]["host"] == "example.com"
    assert {s["engine"] for s in searches} == {"Google", "DuckDuckGo"}
    alerts = wait_for(lambda: [a for a in api.get("/api/alerts").json() if marker in a["detail"]], 10, what="alert")
    assert alerts[0]["kind"] == "search_bypass" and alerts[0]["profile"] == "Kid"


def test_keyword_block_page(house, api):
    body = out("kid-pc", f'curl -s -m 6 {NAV} "https://example.com/?topic=zebrablock"')
    assert "This page is blocked" in body and "zebrablock" in body
    assert fetch_code("kid-pc", "https://example.com/?topic=zebrablockade") == "200"  # whole words only
    wait_for(lambda: any(a["kind"] == "keyword_block" for a in api.get("/api/alerts").json()), 10, what="keyword alert")


def test_uninspected_device_keeps_real_certificates(house):
    issuer = out("kid-console", "curl -sv -m 8 https://example.com/ -o /dev/null 2>&1 | grep -i 'issuer:'")
    assert issuer and "Breakwater" not in issuer


def test_pinned_app_falls_back_to_passthrough(house, api):
    """A client that rejects our certificate fails once, then is let through untouched."""
    pinned = "--cacert /tmp/orig-bundle.crt"
    assert fetch_code("kid-pc", "https://www.wikipedia.org/", pinned) == "000"
    wait_for(lambda: fetch_code("kid-pc", "https://www.wikipedia.org/", pinned) in ("200", "301", "302"), 15,
             what="passthrough after a refused certificate")
    wait_for(lambda: any(p["host"] == "www.wikipedia.org" for p in api.get("/api/system").json()["pinned"]), 10,
             what="pinned host reported")


# --------------------------------------------------------------------------- locks and schedules
def test_lock_and_unlock_a_device(house, api):
    did = house["ids"]["kid-console"]
    assert fetch_code("kid-console", "https://example.com/") == "200"
    api.post("/api/lock", json={"target": "device", "id": did, "minutes": 10}).raise_for_status()
    wait_for(lambda: fetch_code("kid-console", "https://example.com/") == "000", 10, what="lock to bite")
    assert "Internet is paused" in out("kid-console", "curl -s -m 5 http://example.com/")
    assert fetch_code("kid-pc", "https://example.com/") == "200"  # only that device
    api.post("/api/unlock", json={"target": "device", "id": did}).raise_for_status()
    wait_for(lambda: fetch_code("kid-console", "https://example.com/") == "200", 10, what="unlock")


def test_bedtime_locks_and_bonus_time_lifts_it(house, api):
    now = datetime.now(ZoneInfo("America/Vancouver"))
    day = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
    window = [(now - timedelta(minutes=5)).strftime("%H:%M"), (now + timedelta(minutes=30)).strftime("%H:%M")]
    api.put(f"/api/profiles/{house['kid']}", json={"policy": {"bedtime": {"enabled": True, "days": {day: window}}}}).raise_for_status()
    try:
        wait_for(lambda: fetch_code("kid-pc", "https://example.com/") == "000", 12, what="bedtime lock")
        assert "bedtime" in out("kid-pc", "curl -s -m 5 http://example.com/").lower()
        prof = next(p for p in api.get("/api/overview").json()["profiles"] if p["id"] == house["kid"])
        assert prof["locked"] and prof["lock_reason"] == "bedtime"
        api.post("/api/bonus", json={"profile_id": house["kid"], "minutes": 5}).raise_for_status()
        wait_for(lambda: fetch_code("kid-pc", "https://example.com/") == "200", 12, what="bonus time")
    finally:
        api.put(f"/api/profiles/{house['kid']}", json={"policy": {"bedtime": {"enabled": False, "days": {}}}})
        api.post("/api/bonus", json={"profile_id": house["kid"], "minutes": 0})


def test_adult_soft_lock_via_dns(house, api):
    """Adults route around the gateway, so pausing one is done at the filter."""
    did = house["ids"]["adult"]
    api.post("/api/lock", json={"target": "device", "id": did}).raise_for_status()
    try:
        wait_for(lambda: is_blocked(resolve("adult", "example.org")), 15, what="DNS soft lock")
    finally:
        api.post("/api/unlock", json={"target": "device", "id": did})
    wait_for(lambda: not is_blocked(resolve("adult", "example.org")), 15, what="soft lock lifted")


# --------------------------------------------------------------------------- monitoring
def test_activity_reaches_the_dashboard(house, api):
    for _ in range(4):
        resolve("kid-console", ADULT_SITE)
    dx("kid-console", 'curl -s -m 30 -o /dev/null "https://speed.cloudflare.com/__down?bytes=8000000"', timeout=45)

    def seen():
        act = api.get(f"/api/profiles/{house['kid']}/activity").json()
        sites = {s["site"] for s in act["sites"]}
        blocked = {(b["site"], b["reason"]) for b in act["blocked"]}
        return act if "example.com" in sites and (ADULT_SITE, "adult") in blocked else None
    act = wait_for(seen, 40, every=3, what="DNS activity ingested")
    assert act["monitoring"] == "full"
    alerts = api.get("/api/alerts").json()
    assert any(a["kind"] == "blocked_adult" for a in alerts)
    wait_for(lambda: api.get(f"/api/profiles/{house['kid']}/activity").json()["data_bytes"] > 7_000_000, 90, every=5,
             what="traffic counters")


def test_adult_with_monitoring_off_leaves_no_trace(house, api):
    resolve("adult", "example.net")
    time.sleep(12)
    assert api.get(f"/api/profiles/{house['adult']}/activity").json() == {"monitoring": "off"}
    count = out("core1", "python -c \"import sqlite3;print(sqlite3.connect('/data/breakwater.db').execute("
                         f"\\\"select count(*) from activity where mac='{MAC['adult']}'\\\").fetchone()[0])\"")
    assert count == "0"


# --------------------------------------------------------------------------- failover (keep last)
def test_standby_takes_over_and_merges_back(house, api):
    standby = httpx.Client(base_url=NODE2, timeout=15)
    wait_for(lambda: standby.post("/api/login", json=USER).status_code == 200, 30, what="login replicated to standby")
    assert standby.post("/api/profiles", json={"name": "Nope"}).status_code == 409  # settings are primary-only
    nodes = {n["node_id"]: n for n in api.get("/api/system").json()["nodes"]}
    assert nodes["node1"]["active"] and nodes["node2"]["online"] and not nodes["node2"]["active"]

    compose("stop", "node1", "core1", "adguard-strict-1", "adguard-basic-1")
    try:
        wait_for(lambda: fetch_code("kid-console", "https://example.com/") == "200", 20, what="internet via standby")
        assert is_blocked(resolve("kid-console", ADULT_SITE))  # still filtered
        assert VIP in out("node2", "ip -4 -o addr show")
        did = house["ids"]["kid-console"]
        standby.post("/api/lock", json={"target": "device", "id": did}).raise_for_status()
        wait_for(lambda: fetch_code("kid-console", "https://example.com/") == "000", 10, what="lock on standby")
    finally:
        compose("up", "-d")
    wait_for(lambda: api.get("/healthz").status_code == 200, 60, what="node1 back")
    assert api.post("/api/login", json=USER).status_code == 200

    def merged():
        dev = next(d for d in api.get("/api/devices").json() if d["mac"] == MAC["kid-console"])
        return dev["locked"]
    wait_for(merged, 30, what="standby's lock replicated to the primary")
    wait_for(lambda: VIP in out("node1", "ip -4 -o addr show"), 40, what="primary to reclaim the virtual IP")
    api.post("/api/unlock", json={"target": "device", "id": house["ids"]["kid-console"]}).raise_for_status()
    wait_for(lambda: fetch_code("kid-console", "https://example.com/") == "200", 20, what="unlock after failback")
