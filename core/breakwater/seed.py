"""Demo data: a believable four weeks for a made-up family, written through the normal fact pipeline.

Run with BW_DEMO=1. Nothing here touches a real network; it exists so the dashboard can be
explored (and tested) before any device is connected.
"""
from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import bcrypt

from . import policy
from .engine import Engine
from .facts import BUCKET

SITES = {
    "streaming": ["www.youtube.com", "www.netflix.com", "open.spotify.com", "www.twitch.tv", "www.disneyplus.com"],
    "social": ["www.tiktok.com", "www.instagram.com", "www.snapchat.com", "www.reddit.com", "www.pinterest.com"],
    "games": ["www.roblox.com", "www.minecraft.net", "store.steampowered.com", "www.epicgames.com", "poki.com",
              "www.coolmathgames.com"],
    "chat": ["discord.com", "web.whatsapp.com"],
    "learning": ["www.khanacademy.org", "classroom.google.com", "docs.google.com", "en.wikipedia.org",
                 "scratch.mit.edu", "quizlet.com", "www.desmos.com"],
    "other": ["www.google.com", "www.nhl.com", "www.lego.com", "weather.gc.ca", "minecraft.fandom.com",
              "www.allrecipes.com"],
    "ai": ["chatgpt.com"],
    "shopping": ["www.amazon.ca"],
}
DEMO_PASSWORD = "breakwater-demo"
NOISE = ["www.gstatic.com", "play.googleapis.com", "ctldl.windowsupdate.com", "clients.google.com"]

# name, kind, color, tier, monitoring, devices[(mac, name, kind, inspect, weights, intensity)]
FAMILY: list[dict[str, Any]] = [
    {"name": "Maya", "kind": "kid", "color": "coral", "limit": 180, "devices": [
        ("02:bw:00:00:01:01", "Maya's phone", "phone", False,
         {"social": 5, "streaming": 4, "chat": 3, "other": 1, "games": 1}, 1.0),
        ("02:bw:00:00:01:02", "Maya's school laptop", "laptop", True,
         {"learning": 6, "streaming": 2, "other": 2, "ai": 1, "social": 1}, 0.7)]},
    {"name": "Leo", "kind": "kid", "color": "teal", "limit": 120, "devices": [
        ("02:bw:00:00:02:01", "Leo's tablet", "tablet", False,
         {"games": 5, "streaming": 4, "other": 1, "chat": 1}, 0.8),
        ("02:bw:00:00:02:02", "Leo's school laptop", "laptop", True,
         {"learning": 5, "games": 3, "streaming": 2, "other": 2}, 0.6),
        ("02:bw:00:00:02:03", "Nintendo Switch", "console", False, {"games": 9, "streaming": 1}, 0.45)]},
    {"name": "Ava", "kind": "kid", "color": "amber", "limit": 90, "devices": [
        ("02:bw:00:00:03:01", "Ava's Chromebook", "laptop", True,
         {"learning": 5, "streaming": 3, "games": 3, "other": 1}, 0.55)]},
    {"name": "Parents", "kind": "adult", "color": "blue", "devices": [
        ("02:bw:00:00:04:01", "Dana's phone", "phone", False, {}, 0),
        ("02:bw:00:00:04:02", "Work laptop", "laptop", False, {}, 0)]},
    {"name": "Household", "kind": "adult", "color": "slate", "devices": [
        ("02:bw:00:00:05:01", "Living room TV", "tv", False, {}, 0),
        ("02:bw:00:00:05:02", "Printer", "other", False, {}, 0)]},
]

SEARCHES = {
    "Maya": ["how to solve quadratic equations", "french revolution causes summary", "best study playlists",
             "volleyball serving tips", "what is photosynthesis", "how to braid hair", "taylor swift tour dates",
             "romeo and juliet act 2 summary", "is tiktok bad for you", "free vpn for school wifi",
             "how to hide apps from parents"],
    "Leo": ["minecraft redstone door", "roblox promo codes", "how big is a blue whale", "nhl standings",
            "how to draw a dragon", "fractions to decimals", "lego technic sets", "why is the sky blue",
            "unblocked games", "fastest car in the world"],
    "Ava": ["cute puppies", "how do rainbows form", "easy origami", "times tables practice", "what do pandas eat",
            "bluey episodes", "how to draw a cat"],
}
VIDEOS = [
    ("dQw4w9WgXcQ", "Building a Survival Base in 100 Days", "BlockCraft", 1421),
    ("9bZkp7q19f0", "Fractions Explained in 10 Minutes", "Math with Mr. J", 612),
    ("kJQP7kiw5Fk", "Top 10 Volleyball Serves", "SportsLab", 488),
    ("3JZ_D3ELwOQ", "How Volcanoes Work", "SciShow Kids", 367),
    ("L_jWHffIx5E", "Drawing a Dragon Step by Step", "Art for Kids Hub", 754),
    ("fJ9rUzIMcZQ", "Obby Speedrun World Record", "RbxRunner", 903),
    ("OPf0YbXqDm0", "Study With Me - 2 Hour Pomodoro", "Quiet Desk", 7200),
    ("YQHsXMglC9A", "The French Revolution in 9 Minutes", "History Shorts", 548),
    ("RgKAFK5djSk", "Puppies Meet Kittens Compilation", "Paws TV", 421),
    ("CevxZvSJLk8", "Hockey Trick Shots", "Ice Lab", 334),
]


def _mac(m: str) -> str:
    return m.replace("bw", "b0")


def _sessions(rng: random.Random, weekday: int, intensity: float) -> list[tuple[float, float]]:
    """(start hour, length hours) blocks for one device-day."""
    weekend = weekday >= 5
    out: list[tuple[float, float]] = []
    if not weekend and rng.random() < 0.5 * intensity:
        out.append((7.1 + rng.random() * 0.5, 0.15 + rng.random() * 0.25))
    slots = [(9.5, 12), (13, 17), (18.5, 21.3)] if weekend else [(15.6, 17.5), (18.6, 20.9)]
    for lo, hi in slots:
        if rng.random() < 0.55 + 0.4 * intensity:
            start = lo + rng.random() * (hi - lo) * 0.5
            length = (0.3 + rng.random() * 1.3) * (1.25 if weekend else 1) * (0.5 + intensity)
            out.append((start, min(length, hi - start + 0.4)))
    return out


def seed_history(eng: Engine, days: int = 28, seed: int = 7) -> None:
    rng = random.Random(seed)
    db, tz = eng.db, eng.cfg.tz
    now = eng.now()
    eng.db.meta_set("installed_at", time.time() - (days + 1) * 86400)

    with db.tx() as c:
        for sort, fam in enumerate(FAMILY):
            kid = fam["kind"] == "kid"
            pol: dict[str, Any] = {}
            if kid:
                pol = policy.default_policy("kid")
                pol["daily_limit"] = {"enabled": True, "minutes": {d: fam["limit"] + (60 if d in ("sat", "sun") else 0)
                                                                 for d in policy.DAYS}}
                pol["blocked_services"] += ["tiktok"] if fam["name"] != "Maya" else []
                pol["keywords"] = ["unblocked"] if fam["name"] == "Leo" else []
            cur = c.execute("INSERT INTO profiles(name,kind,tier,monitoring,color,policy,sort,created) VALUES(?,?,?,?,?,?,?,?)",
                            (fam["name"], fam["kind"], "strict" if kid else "basic", "full" if kid else "off",
                             fam["color"], json.dumps(pol), sort, time.time()))
            for i, (mac, name, kind, inspect, _, _) in enumerate(fam["devices"]):
                c.execute("INSERT INTO devices(mac,name,kind,profile_id,inspect,ip,hostname,created) VALUES(?,?,?,?,?,?,?,?)",
                          (_mac(mac), name, kind, cur.lastrowid, int(inspect), f"192.168.0.{50 + sort * 10 + i}",
                           name.lower().replace(" ", "-").replace("'", ""), time.time() - days * 86400))
        c.execute("INSERT INTO devices(mac,name,kind,hostname,created) VALUES(?,?,?,?,?)",
                  ("02:b0:00:00:09:01", "", "phone", "Galaxy-A54", time.time() - 3600))
        c.execute("INSERT INTO users(username,pw_hash) VALUES('demo', ?)",
                  (bcrypt.hashpw(DEMO_PASSWORD.encode(), bcrypt.gensalt(6)).decode(),))
        db.bump_config()

    for fam in FAMILY:
        if fam["kind"] != "kid":
            continue
        for mac, name, kind, inspect, weights, intensity in fam["devices"]:
            mac = _mac(mac)
            cats = list(weights)
            for back in range(days, -1, -1):
                day = (now - timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0)
                dns_rows: list[list[Any]] = []
                usage_rows: list[list[Any]] = []
                web: list[dict[str, Any]] = []
                for start_h, length_h in _sessions(rng, day.weekday(), intensity):
                    t = int((day + timedelta(hours=start_h)).timestamp())
                    t -= t % BUCKET
                    end = t + int(length_h * 3600)
                    site = host = ""
                    stick = 0
                    while t < end and t < now.timestamp() - 120:
                        if stick <= 0:
                            cat = rng.choices(cats, [weights[c] for c in cats])[0]
                            host = rng.choice(SITES[cat])
                            site, category = eng.cat.classify(host)
                            stick = rng.randint(2, 9)
                        if inspect:
                            # what an inspected device adds: pages, videos and searches
                            if rng.random() < 0.45:
                                web.append({"t": t + rng.randint(5, 200), "mac": mac, "k": "visit", "host": host,
                                            "url": f"https://{host}/", "title": eng.cat.label(site)})
                            if host == "www.youtube.com" and rng.random() < 0.6:
                                vid = rng.choice(VIDEOS)
                                web.append({"t": t + rng.randint(20, 250), "mac": mac, "k": "video", "host": host,
                                            "vid": vid[0], "title": vid[1], "channel": vid[2], "dur": vid[3]})
                            if rng.random() < (0.5 if host == "www.google.com" else 0.07):
                                web.append({"t": t + rng.randint(5, 250), "mac": mac, "k": "search",
                                            "host": "www.google.com", "engine": "Google",
                                            "q": rng.choice(SEARCHES[fam["name"]])})
                        stick -= 1
                        dns_rows.append([mac, t, site, category, rng.randint(5, 22), 0, ""])
                        if rng.random() < 0.35:
                            s2, c2 = eng.cat.classify(rng.choice(SITES[rng.choice(cats)]))
                            if s2 != site:
                                dns_rows.append([mac, t, s2, c2, rng.randint(1, 4), 0, ""])
                        ns, nc = eng.cat.classify(rng.choice(NOISE))
                        dns_rows.append([mac, t, ns, nc, rng.randint(2, 9), rng.randint(0, 2), "ads"])
                        for minute in range(t, t + BUCKET, 60):
                            heavy = category in ("streaming", "games")
                            usage_rows.append([mac, minute, rng.randint(20_000, 400_000),
                                               rng.randint(2_000_000, 30_000_000) if heavy else rng.randint(150_000, 2_500_000)])
                        t += BUCKET
                # a few blocked attempts, some days
                if kind in ("phone", "laptop") and rng.random() < 0.12:
                    t = int((day + timedelta(hours=16 + rng.random() * 4)).timestamp())
                    t -= t % BUCKET
                    if t < now.timestamp():
                        reason, host = rng.choice([("adult", "pornhub.com"), ("bypass", "nordvpn.com"),
                                                   ("adult", "xvideos.com"), ("service", "@tiktok")])
                        dns_rows.append([mac, t, host, "other", 4, 4, reason])
                ts = min(day.timestamp() + 86399, now.timestamp())
                if dns_rows:
                    eng.facts.append("dns", {"rows": dns_rows}, ts)
                if usage_rows:
                    eng.facts.append("usage", {"rows": usage_rows}, ts)
                if web:
                    eng.facts.append("web", {"items": web}, ts)
    while eng.facts.recompute_dirty(5000):
        pass
    eng.facts.append("lease", {"mac": "02:b0:00:00:09:01", "ip": "192.168.0.143", "hostname": "Galaxy-A54"})
    with db.tx() as c:  # leave a handful unread so the dashboard has something to show
        c.execute("UPDATE alerts SET acked=1 WHERE ts<?", (time.time() - 2 * 86400,))


def _ticker(eng: Engine) -> None:
    """Keep two demo devices looking live."""
    rng = random.Random()
    live = [("02:b0:00:00:01:01", "www.youtube.com"), ("02:b0:00:00:02:03", "www.roblox.com")]
    while True:
        now = int(time.time())
        minute = now - now % 60
        rows, usage = [], []
        for mac, host in live:
            site, cat = eng.cat.classify(host)
            rows.append([mac, minute - minute % BUCKET, site, cat, rng.randint(2, 6), 0, ""])
            usage.append([mac, minute, 200_000, rng.randint(3_000_000, 9_000_000)])
        eng.facts.append("dns", {"rows": rows})
        eng.facts.append("usage", {"rows": usage})
        eng.facts.recompute_dirty()
        eng.invalidate()
        for _ in range(12):  # keep the pretend gateway's heartbeat fresh between ticks
            eng.gateway_status = {"vip_held": True, "dhcp": True, "proxy": True, "ts": time.time()}
            time.sleep(5)


def run(eng: Engine) -> None:
    if not eng.db.val("SELECT COUNT(*) FROM profiles", (), 0):
        seed_history(eng)
    threading.Thread(target=_ticker, args=(eng,), daemon=True, name="demo-ticker").start()
