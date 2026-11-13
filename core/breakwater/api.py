"""HTTP API: the parent dashboard, the gateway's internal channel and node-to-node sync."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

import bcrypt
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import policy, queries
from .config import Config
from .engine import Engine

COOKIE = "bw_session"
SESSION_TTL = 30 * 86400
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
PUBLIC_PATHS = ("/ca.pem", "/ca.crt", "/ca.cer", "/healthz")
DEVICE_KINDS = ("phone", "tablet", "laptop", "desktop", "console", "tv", "other")
COLORS = ("teal", "coral", "amber", "violet", "blue", "green", "rose", "slate")
SETTINGS_DEFAULTS: dict[str, Any] = {
    "quarantine_unknown": True, "ntfy_url": "", "watch_terms": [], "passthrough_hosts": [],
    "detail_days": 30, "rollup_days": 90,
}


def clean_domain(value: str) -> str | None:
    v = value.strip().lower()
    v = re.sub(r"^[a-z]+://", "", v).split("/")[0].split(":")[0].lstrip("*.").rstrip(".")
    return v if DOMAIN_RE.match(v) else None


def create_app(cfg: Config, eng: Engine, web_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Breakwater", docs_url=None, redoc_url=None, openapi_url=None)
    db = eng.db
    key_file = cfg.data_dir / "session.key"
    if not key_file.exists():
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(secrets.token_bytes(32))
        os.chmod(key_file, 0o600)
    session_key = key_file.read_bytes()
    failed: dict[str, list[float]] = {}
    ca_dir = Path(os.environ.get("BW_CA_DIR", "/ca"))

    # ------------------------------------------------------------------ auth
    def sign(username: str) -> str:
        body = f"{username}|{int(time.time()) + SESSION_TTL}"
        mac = hmac.new(session_key, body.encode(), hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(f"{body}|{mac}".encode()).decode()

    def session_user(request: Request) -> str | None:
        raw = request.cookies.get(COOKIE)
        if not raw:
            return None
        try:
            username, exp, mac = base64.urlsafe_b64decode(raw.encode()).decode().rsplit("|", 2)
        except Exception:
            return None
        good = hmac.new(session_key, f"{username}|{exp}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, good) or int(exp) < time.time():
            return None
        return username if db.one("SELECT 1 FROM users WHERE username=?", (username,)) else None

    def parent(request: Request) -> str:
        user = session_user(request)
        if not user:
            raise HTTPException(401, "Sign in required")
        return user

    def primary_only() -> None:
        if not cfg.is_primary:
            raise HTTPException(409, "This node is a standby. Settings can only be changed on the primary node.")

    def node_token(request: Request) -> None:
        if not cfg.token or not hmac.compare_digest(request.headers.get("X-BW-Token", ""), cfg.token):
            raise HTTPException(403, "Bad node token")

    @app.middleware("http")
    async def refuse_kid_devices(request: Request, call_next):
        path = request.url.path
        if not (path in PUBLIC_PATHS or path.startswith(("/internal/", "/cluster/"))):
            ip = request.client.host if request.client else ""
            row = db.one("SELECT p.kind FROM devices d JOIN profiles p ON p.id=d.profile_id "
                         "WHERE d.ip=? OR d.mac=(SELECT mac FROM leases WHERE ip=?)", (ip, ip))
            if row and row["kind"] == "kid":
                return PlainTextResponse("This page is for parents.", status_code=403)
        return await call_next(request)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "node": cfg.node_id, "role": cfg.role}

    @app.get("/api/session")
    def get_session(request: Request):
        user = session_user(request)
        return {"authenticated": bool(user), "username": user, "role": cfg.role, "node_id": cfg.node_id,
                "setup_needed": db.val("SELECT COUNT(*) FROM users", (), 0) == 0, "tz": cfg.tz_name,
                "utc_offset": int(eng.now().utcoffset().total_seconds())}

    def _check_password(pw: str) -> None:
        if len(pw) < 10:
            raise HTTPException(400, "Use a password of at least 10 characters.")

    @app.post("/api/setup")
    def setup(body: dict[str, Any], response: Response):
        primary_only()
        username, pw = str(body.get("username", "")).strip(), str(body.get("password", ""))
        if not username:
            raise HTTPException(400, "Choose a username.")
        _check_password(pw)
        with db.tx() as c:  # check and insert together so two racing requests can't both win
            if db.val("SELECT COUNT(*) FROM users", (), 0):
                raise HTTPException(409, "Breakwater is already set up.")
            c.execute("INSERT INTO users(username,pw_hash) VALUES(?,?)",
                      (username, bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()))
            db.bump_config()
        response.set_cookie(COOKIE, sign(username), max_age=SESSION_TTL, httponly=True, samesite="lax")
        return {"ok": True}

    @app.post("/api/login")
    def login(body: dict[str, Any], request: Request, response: Response):
        ip = request.client.host if request.client else "?"
        recent = [t for t in failed.get(ip, []) if time.time() - t < 600]
        if len(recent) >= 8:
            raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
        row = db.one("SELECT * FROM users WHERE username=?", (str(body.get("username", "")).strip(),))
        if not row or not bcrypt.checkpw(str(body.get("password", "")).encode(), row["pw_hash"].encode()):
            failed[ip] = recent + [time.time()]
            raise HTTPException(401, "Wrong username or password.")
        failed.pop(ip, None)
        response.set_cookie(COOKIE, sign(row["username"]), max_age=SESSION_TTL, httponly=True, samesite="lax")
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.put("/api/account/password")
    def change_password(body: dict[str, Any], user: str = Depends(parent)):
        primary_only()
        row = db.one("SELECT * FROM users WHERE username=?", (user,))
        if not bcrypt.checkpw(str(body.get("current", "")).encode(), row["pw_hash"].encode()):
            raise HTTPException(400, "Current password is wrong.")
        _check_password(str(body.get("new", "")))
        with db.tx() as c:
            c.execute("UPDATE users SET pw_hash=? WHERE id=?",
                      (bcrypt.hashpw(str(body["new"]).encode(), bcrypt.gensalt()).decode(), row["id"]))
            db.bump_config()
        return {"ok": True}

    # ------------------------------------------------------------- dashboard
    @app.get("/api/overview")
    def get_overview(_: str = Depends(parent)):
        return queries.overview(eng)

    @app.get("/api/catalogue")
    def get_catalogue(_: str = Depends(parent)):
        return eng.cat.public()

    def _clean_policy(pol: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        valid_services = set(eng.cat.services) | {f"group:{g}" for g in eng.cat.groups}

        def services(v: Any) -> list[str]:
            return sorted({s for s in (v or []) if s in valid_services})

        def hhmm(v: Any) -> str:
            m = re.match(r"^(\d{1,2}):(\d{2})$", str(v))
            if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                raise HTTPException(400, f"Bad time: {v!r}")
            return f"{int(m.group(1)):02d}:{m.group(2)}"

        for k, v in pol.items():
            if k == "safesearch":
                out[k] = bool(v)
            elif k == "youtube":
                out[k] = v if v in ("off", "moderate", "strict") else "moderate"
            elif k == "blocked_services":
                out[k] = services(v)
            elif k in ("allow_domains", "block_domains"):
                out[k] = sorted({d for d in (clean_domain(x) for x in v or []) if d})
            elif k == "keywords":
                out[k] = sorted({str(x).strip().lower()[:60] for x in v or [] if str(x).strip()})
            elif k == "bedtime":
                out[k] = {"enabled": bool(v.get("enabled")),
                          "days": {d: ([hhmm(w[0]), hhmm(w[1])] if w else None)
                                   for d, w in ((d, (v.get("days") or {}).get(d)) for d in policy.DAYS)}}
            elif k == "daily_limit":
                mins = v.get("minutes") or {}
                out[k] = {"enabled": bool(v.get("enabled")),
                          "minutes": {d: (max(0, min(1440, int(mins[d]))) if mins.get(d) is not None else None)
                                      for d in policy.DAYS}}
            elif k == "homework":
                wins = v.get("windows") or {}
                out[k] = {"enabled": bool(v.get("enabled")), "services": services(v.get("services")),
                          "windows": {d: [[hhmm(w[0]), hhmm(w[1])] for w in wins.get(d) or []] for d in policy.DAYS}}
        return out

    def _profile_out(p: dict[str, Any]) -> dict[str, Any]:
        return {**p, "policy": policy.merged_policy(p, None)}

    @app.get("/api/profiles")
    def list_profiles(_: str = Depends(parent)):
        return [_profile_out(p) for p in eng.profiles()]

    def _profile_fields(body: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
        kind = body.get("kind", (existing or {}).get("kind", "kid"))
        if kind not in ("kid", "adult"):
            raise HTTPException(400, "kind must be kid or adult")
        name = str(body.get("name", (existing or {}).get("name", ""))).strip()[:40]
        if not name:
            raise HTTPException(400, "Give the profile a name.")
        tier = body.get("tier", (existing or {}).get("tier", "strict" if kind == "kid" else "basic"))
        mon = body.get("monitoring", (existing or {}).get("monitoring", "full" if kind == "kid" else "off"))
        if tier not in policy.TIERS or mon not in policy.MONITORING:
            raise HTTPException(400, "Bad tier or monitoring level")
        color = body.get("color", (existing or {}).get("color", "teal"))
        pol = dict((existing or {}).get("policy") or policy.default_policy(kind))
        if "policy" in body:
            pol.update(_clean_policy(body["policy"]))
        return {"name": name, "kind": kind, "tier": tier, "monitoring": mon,
                "color": color if color in COLORS else "teal", "policy": json.dumps(pol)}

    @app.post("/api/profiles")
    def create_profile(body: dict[str, Any], _: str = Depends(parent)):
        primary_only()
        f = _profile_fields(body, None)
        with db.tx() as c:
            cur = c.execute("INSERT INTO profiles(name,kind,tier,monitoring,color,policy,sort,created) VALUES(?,?,?,?,?,?,?,?)",
                            (f["name"], f["kind"], f["tier"], f["monitoring"], f["color"], f["policy"],
                             db.val("SELECT COALESCE(MAX(sort),0)+1 FROM profiles WHERE sort<1000", (), 1), time.time()))
            db.bump_config()
        eng.invalidate()
        return {"id": cur.lastrowid}

    def _get_profile(pid: int) -> dict[str, Any]:
        p = next((p for p in eng.profiles() if p["id"] == pid), None)
        if not p:
            raise HTTPException(404, "No such profile")
        return p

    @app.put("/api/profiles/{pid}")
    def update_profile(pid: int, body: dict[str, Any], _: str = Depends(parent)):
        primary_only()
        f = _profile_fields(body, _get_profile(pid))
        with db.tx() as c:
            c.execute("UPDATE profiles SET name=?,kind=?,tier=?,monitoring=?,color=?,policy=? WHERE id=?",
                      (f["name"], f["kind"], f["tier"], f["monitoring"], f["color"], f["policy"], pid))
            db.bump_config()
        eng.invalidate()
        return {"ok": True}

    @app.delete("/api/profiles/{pid}")
    def delete_profile(pid: int, _: str = Depends(parent)):
        primary_only()
        _get_profile(pid)
        with db.tx() as c:
            c.execute("UPDATE devices SET profile_id=NULL, inspect=0 WHERE profile_id=?", (pid,))
            c.execute("DELETE FROM profiles WHERE id=?", (pid,))
            db.bump_config()
        eng.invalidate()
        return {"ok": True}

    def _macs(pid: int) -> list[str]:
        return [r["mac"] for r in db.q("SELECT mac FROM devices WHERE profile_id=?", (pid,))]

    @app.get("/api/profiles/{pid}/activity")
    def get_activity(pid: int, day: str | None = None, _: str = Depends(parent)):
        p = _get_profile(pid)
        if p["monitoring"] == "off":
            return {"monitoring": "off"}
        return {"monitoring": p["monitoring"], **queries.profile_activity(eng, pid, day)}

    @app.get("/api/profiles/{pid}/events")
    def get_events(pid: int, kind: str, day: str | None = None, limit: int = 200, _: str = Depends(parent)):
        p = _get_profile(pid)
        if kind not in ("visit", "search", "video", "block") or p["monitoring"] != "full":
            return []
        start = end = None
        if day:
            act = queries.profile_activity(eng, pid, day)
            start, end = act["day_start"], act["day_end"]
        return queries.events(eng, _macs(pid), kind, min(limit, 500), day_start=start, day_end=end)

    # --------------------------------------------------------------- devices
    @app.get("/api/devices")
    def list_devices(_: str = Depends(parent)):
        return queries.all_devices(eng)

    @app.put("/api/devices/{did}")
    def update_device(did: int, body: dict[str, Any], _: str = Depends(parent)):
        primary_only()
        d = next((d for d in eng.devices() if d["id"] == did), None)
        if not d:
            raise HTTPException(404, "No such device")
        sets: dict[str, Any] = {}
        if "name" in body:
            sets["name"] = str(body["name"]).strip()[:60]
        if "kind" in body:
            sets["kind"] = body["kind"] if body["kind"] in DEVICE_KINDS else "other"
        if "profile_id" in body:
            pid = body["profile_id"]
            if pid is not None:
                _get_profile(int(pid))
            sets["profile_id"] = pid
            if pid is not None and not d["ip"] and d["addr"]:
                sets["ip"] = d["addr"]  # give assigned devices a fixed address so policy follows them
        if "route" in body:
            sets["route"] = body["route"] if body["route"] in ("gateway", "router") else None
        if "inspect" in body:
            sets["inspect"] = 1 if body["inspect"] else 0
        if "overrides" in body:
            sets["overrides"] = json.dumps(_clean_policy(body["overrides"] or {}))
        if sets:
            with db.tx() as c:
                c.execute(f"UPDATE devices SET {','.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), did))
                db.bump_config()
        eng.invalidate()
        return {"ok": True}

    @app.delete("/api/devices/{did}")
    def forget_device(did: int, _: str = Depends(parent)):
        primary_only()
        with db.tx() as c:
            row = db.one("SELECT mac FROM devices WHERE id=?", (did,))
            if row:
                c.execute("DELETE FROM devices WHERE id=?", (did,))
                c.execute("DELETE FROM leases WHERE mac=?", (row["mac"],))
                db.bump_config()
        eng.invalidate()
        return {"ok": True}

    # ----------------------------------------------------------------- locks
    def _target(body: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        if body.get("target") == "profile":
            p = _get_profile(int(body["id"]))
            return f"p:{p['id']}", p
        d = next((d for d in eng.devices() if d["id"] == int(body.get("id", 0))), None)
        if not d:
            raise HTTPException(404, "No such device")
        prof = next((p for p in eng.profiles() if p["id"] == d["profile_id"]), None)
        return f"d:{d['mac']}", prof

    @app.post("/api/lock")
    def lock(body: dict[str, Any], _: str = Depends(parent)):
        target, prof = _target(body)
        until = 0.0
        if body.get("until") == "morning":
            until = eng.lock_until_morning(prof)
        elif body.get("minutes"):
            until = time.time() + max(1, min(24 * 60, int(body["minutes"]))) * 60
        eng.act({"type": "lock", "target": target, "until": until})
        return {"ok": True, "until": until}

    @app.post("/api/unlock")
    def unlock(body: dict[str, Any], _: str = Depends(parent)):
        target, prof = _target(body)
        eng.act({"type": "unlock", "target": target})
        if target.startswith("p:"):  # lifting a profile lock also lifts locks on its devices
            for mac in _macs(prof["id"]):
                if db.one("SELECT 1 FROM locks WHERE target=? AND locked=1", (f"d:{mac}",)):
                    eng.act({"type": "unlock", "target": f"d:{mac}"})
        return {"ok": True}

    @app.post("/api/bonus")
    def bonus(body: dict[str, Any], _: str = Depends(parent)):
        p = _get_profile(int(body["profile_id"]))
        minutes = max(0, min(600, int(body.get("minutes", 30))))
        eng.act({"type": "bonus", "profile_id": p["id"], "until": time.time() + minutes * 60 if minutes else 0})
        return {"ok": True}

    @app.get("/api/alerts")
    def list_alerts(unacked: bool = False, profile_id: int | None = None, limit: int = 200, _: str = Depends(parent)):
        macs = _macs(profile_id) if profile_id is not None else None
        return queries.alerts(eng, min(limit, 500), macs, unacked)

    @app.get("/api/alerts/count")
    def count_alerts(_: str = Depends(parent)):
        return {"unacked": db.val("SELECT COUNT(*) FROM alerts WHERE acked=0", (), 0)}

    @app.post("/api/alerts/ack")
    def ack_alert(body: dict[str, Any], _: str = Depends(parent)):
        eng.act({"type": "ack_all"} if body.get("all") else {"type": "ack", "key": str(body["key"])})
        return {"ok": True}

    # -------------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings(_: str = Depends(parent)):
        return {k: db.setting(k, v) for k, v in SETTINGS_DEFAULTS.items()}

    @app.put("/api/settings")
    def put_settings(body: dict[str, Any], _: str = Depends(parent)):
        primary_only()
        for k, v in body.items():
            if k not in SETTINGS_DEFAULTS:
                continue
            if k == "quarantine_unknown":
                v = bool(v)
            elif k == "ntfy_url":
                v = str(v).strip()
                if v and not re.match(r"^https?://\S+$", v):
                    raise HTTPException(400, "The notification address must start with http:// or https://")
            elif k == "watch_terms":
                v = sorted({str(x).strip().lower()[:60] for x in v or [] if str(x).strip()})
            elif k == "passthrough_hosts":
                v = sorted({d for d in (clean_domain(x) for x in v or []) if d})
            elif k in ("detail_days", "rollup_days"):
                v = max(1, min(730, int(v)))
            db.set_setting(k, v)
        eng.invalidate()
        return {"ok": True}

    @app.post("/api/settings/test-notification")
    def test_notification(_: str = Depends(parent)):
        url = db.setting("ntfy_url", "")
        if not url:
            raise HTTPException(400, "Set a notification address first.")
        from .engine import _post_ntfy
        _post_ntfy(url, {"title": "Test notification", "detail": "Notifications are working.", "severity": "medium"},
                   "Breakwater")
        return {"ok": True}

    @app.get("/api/system")
    def get_system(_: str = Depends(parent)):
        return {**queries.system(eng), "ca_available": (ca_dir / "mitmproxy-ca-cert.pem").exists()}

    for path in ("/ca.pem", "/ca.crt", "/ca.cer"):
        @app.get(path)
        def get_ca(path: str = path):
            src = ca_dir / ("mitmproxy-ca-cert.cer" if path.endswith(".cer") else "mitmproxy-ca-cert.pem")
            if not src.exists():
                raise HTTPException(404, "The certificate has not been generated yet.")
            return FileResponse(src, media_type="application/x-x509-ca-cert",
                                filename="breakwater-ca" + path[path.rfind("."):])

    # ---------------------------------------------------- gateway <-> core
    @app.get("/internal/state")
    def internal_state(have: str = "", _: None = Depends(node_token)):
        state = eng.gateway_state()
        if have == state["version"]:
            return Response(status_code=304)
        return state

    @app.post("/internal/report")
    def internal_report(body: dict[str, Any], _: None = Depends(node_token)):
        eng.report(body)
        return {"ok": True, "state": eng.gateway_state()["version"]}

    @app.get("/internal/leases")
    def internal_leases(_: None = Depends(node_token)):
        return [dict(r) for r in db.q("SELECT * FROM leases WHERE expires=0 OR expires>?", (time.time(),))]

    # ---------------------------------------------------------- node <-> node
    @app.post("/cluster/pull")
    def cluster_pull(body: dict[str, Any], _: None = Depends(node_token)):
        if body.get("status"):
            eng.record_node(body["status"])
        return {"facts": eng.facts.since(body.get("vector") or {}), "status": eng.status()}

    @app.get("/cluster/config")
    def cluster_config(_: None = Depends(node_token)):
        return db.config_snapshot()

    # ---------------------------------------------------------------- web UI
    if web_dir and web_dir.exists():
        app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            if path.startswith(("api/", "internal/", "cluster/")):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            f = (web_dir / path).resolve()
            if path and f.is_file() and web_dir.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(web_dir / "index.html", headers={"Cache-Control": "no-cache"})

    return app
