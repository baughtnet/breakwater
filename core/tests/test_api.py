import time

import pytest
from fastapi.testclient import TestClient

from breakwater.api import clean_domain, create_app
from breakwater.config import Config
from breakwater.db import DB
from breakwater.engine import Engine
from breakwater.main import ensure_defaults

TOKEN = {"X-BW-Token": "t0ken"}


def make(tmp_path, role="primary", node="n1"):
    cfg = Config(node_id=node, role=role, token="t0ken", data_dir=tmp_path / node, adguard_enabled=False,
                 tz_name="America/Vancouver")
    eng = Engine(cfg, DB(":memory:"))
    ensure_defaults(eng)
    return eng, TestClient(create_app(cfg, eng))


@pytest.fixture
def node(tmp_path):
    eng, client = make(tmp_path)
    assert client.post("/api/setup", json={"username": "dana", "password": "correct horse battery"}).status_code == 200
    return eng, client


def add_kid(eng, client, mac="aa:bb:cc:00:00:01", ip="192.168.0.50"):
    pid = client.post("/api/profiles", json={"name": "Leo", "kind": "kid",
                                             "policy": {"bedtime": {"enabled": False, "days": {}}}}).json()["id"]
    eng.report({"facts": [{"kind": "lease", "data": {"mac": mac, "ip": ip, "hostname": "leo-tab"}}]})
    did = next(d["id"] for d in client.get("/api/devices").json() if d["mac"] == mac)
    assert client.put(f"/api/devices/{did}", json={"profile_id": pid, "inspect": True, "name": "Leo's tablet"}).status_code == 200
    return pid, did


def test_everything_requires_a_parent_login(tmp_path):
    _, client = make(tmp_path)
    for path in ("/api/overview", "/api/devices", "/api/profiles", "/api/settings", "/api/system", "/api/alerts"):
        assert client.get(path).status_code == 401
    assert client.get("/internal/state").status_code == 403
    assert client.post("/cluster/pull", json={}).status_code == 403
    assert client.get("/api/session").json()["setup_needed"] is True


def test_setup_runs_once_and_login_works(node):
    _, client = node
    assert client.post("/api/setup", json={"username": "kid", "password": "let me in please"}).status_code == 409
    client.cookies.clear()
    assert client.post("/api/login", json={"username": "dana", "password": "nope"}).status_code == 401
    assert client.post("/api/login", json={"username": "dana", "password": "correct horse battery"}).status_code == 200
    assert client.get("/api/session").json()["authenticated"] is True


def test_weak_password_rejected(tmp_path):
    _, client = make(tmp_path)
    assert client.post("/api/setup", json={"username": "dana", "password": "short"}).status_code == 400


def test_device_flows_to_gateway_state_and_lock(node):
    eng, client = node
    pid, did = add_kid(eng, client)
    state = client.get("/internal/state", headers=TOKEN).json()
    dev = next(d for d in state["devices"] if d["mac"] == "aa:bb:cc:00:00:01")
    assert (dev["tier"], dev["route"], dev["inspect"], dev["locked"], dev["ip"]) == ("strict", "gateway", True, False, "192.168.0.50")
    assert client.get(f"/internal/state?have={state['version']}", headers=TOKEN).status_code == 304

    assert client.post("/api/lock", json={"target": "profile", "id": pid, "minutes": 30}).status_code == 200
    dev = client.get("/internal/state", headers=TOKEN).json()["devices"][0]
    assert dev["locked"] and dev["lock_reason"] == "manual" and dev["lock_until"] > time.time() + 1700
    assert client.get("/api/overview").json()["profiles"][0]["locked"] is True

    client.post("/api/unlock", json={"target": "profile", "id": pid})
    assert client.get("/internal/state", headers=TOKEN).json()["devices"][0]["locked"] is False
    client.post("/api/lock", json={"target": "device", "id": did})
    assert client.get("/api/devices").json()[0]["lock_until"] == 0  # until unlocked


def test_kid_devices_cannot_open_the_dashboard(node):
    eng, client = node
    add_kid(eng, client, ip="testclient")  # TestClient's source address
    assert client.get("/api/overview").status_code == 403
    assert client.get("/api/session").status_code == 403
    assert client.get("/healthz").status_code == 200


def test_policy_input_is_sanitised(node):
    eng, client = node
    pid, _ = add_kid(eng, client)
    r = client.put(f"/api/profiles/{pid}", json={"policy": {
        "block_domains": ["https://Reddit.com/r/all", "bad^$important", "*.example.org"],
        "blocked_services": ["tiktok", "not-a-service", "group:gaming"],
        "keywords": ["  Fortnite ", ""]}})
    assert r.status_code == 200
    pol = client.get("/api/profiles").json()[0]["policy"]
    assert pol["block_domains"] == ["example.org", "reddit.com"]
    assert pol["blocked_services"] == ["group:gaming", "tiktok"]
    assert pol["keywords"] == ["fortnite"]
    assert client.put(f"/api/profiles/{pid}", json={"policy": {"bedtime": {"enabled": True, "days": {"mon": ["25:00", "07:00"]}}}}).status_code == 400
    assert clean_domain("evil.com$client=1.2.3.4") is None


def test_monitoring_off_hides_activity(node):
    eng, client = node
    pid = client.post("/api/profiles", json={"name": "Dana", "kind": "adult"}).json()["id"]
    assert client.get(f"/api/profiles/{pid}/activity").json() == {"monitoring": "off"}
    assert client.get(f"/api/profiles/{pid}/events?kind=search").json() == []


def test_standby_replicates_config_and_facts_and_refuses_edits(tmp_path, node):
    primary, pclient = node
    pid, _ = add_kid(primary, pclient)
    standby, sclient = make(tmp_path, role="standby", node="n2")

    # what the standby's cluster loop does, with the HTTP hop replaced by the test client
    body = pclient.post("/cluster/pull", json={"vector": standby.facts.vector(), "status": standby.status()}, headers=TOKEN).json()
    standby.facts.ingest(body["facts"])
    standby.db.load_config_snapshot(pclient.get("/cluster/config", headers=TOKEN).json())
    standby.invalidate()

    assert sclient.post("/api/login", json={"username": "dana", "password": "correct horse battery"}).status_code == 200
    assert [p["name"] for p in sclient.get("/api/profiles").json()] == ["Leo", "Household"]
    assert sclient.get("/internal/state", headers=TOKEN).json()["devices"][0]["ip"] == "192.168.0.50"
    assert sclient.post("/api/profiles", json={"name": "X"}).status_code == 409
    # emergency controls still work on a standby, and flow back to the primary
    assert sclient.post("/api/lock", json={"target": "profile", "id": pid}).status_code == 200
    back = sclient.post("/cluster/pull", json={"vector": primary.facts.vector()}, headers=TOKEN).json()
    primary.facts.ingest(back["facts"])
    primary.invalidate()
    assert pclient.get("/internal/state", headers=TOKEN).json()["devices"][0]["locked"] is True
    assert {n["node_id"] for n in pclient.get("/api/system").json()["nodes"]} == {"n1", "n2"}


def test_demo_seed_produces_a_full_dashboard(tmp_path):
    from breakwater import seed
    cfg = Config(node_id="demo", token="t", data_dir=tmp_path, adguard_enabled=False)
    eng = Engine(cfg, DB(":memory:"))
    seed.seed_history(eng, days=3)
    client = TestClient(create_app(cfg, eng))
    assert client.post("/api/login", json={"username": "demo", "password": seed.DEMO_PASSWORD}).status_code == 200
    ov = client.get("/api/overview").json()
    kids = [p for p in ov["profiles"] if p["kind"] == "kid"]
    assert len(kids) == 3 and all(len(p["hours"]) == 24 for p in kids)
    act = client.get(f"/api/profiles/{kids[0]['id']}/activity").json()
    assert act["trend"] and len(act["heatmap"]) == 7 and act["monitoring"] == "full"
    assert sum(t["total"] for t in act["trend"]) > 0
