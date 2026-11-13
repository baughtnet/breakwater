"""mitmproxy addon: the inspecting half of the gateway.

Only devices a parent switched to "inspect" are ever redirected here. For those it records page
visits, searches and YouTube videos, enforces keyword blocks and strict safe-search, and steps
out of the way (plain TCP passthrough) for hosts that pin certificates or are on the bypass list.

Nothing else is kept: no request bodies, no cookies, no form data.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from collections import deque
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from mitmproxy import http, tls

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bw_gateway import extract  # noqa: E402

STATE_FILE = Path(os.environ.get("BW_STATE_FILE", "/run/breakwater/state.json"))
CORE_URL = os.environ.get("BW_CORE_URL", "http://127.0.0.1:8080").rstrip("/")
TOKEN = os.environ.get("BW_CLUSTER_TOKEN", "")
PIN_TTL = 24 * 3600
MAX_BUFFER = 3 * 1024 * 1024

BLOCK_HTML = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blocked</title><style>body{{font:16px/1.5 system-ui,sans-serif;background:#0d2530;color:#e8f1f2;display:grid;
place-items:center;min-height:100vh;margin:0}}main{{max-width:28rem;padding:2rem;text-align:center}}
h1{{font-size:1.5rem;margin:0 0 .5rem}}p{{color:#a9c1c7;margin:.25rem 0}}</style>
<main><h1>This page is blocked</h1><p>{reason}</p><p>If you think this is a mistake, ask a parent.</p></main>"""


class Breakwater:
    def __init__(self) -> None:
        self.by_ip: dict[str, dict] = {}
        self.passthrough: frozenset[str] = frozenset()
        self._mtime = 0.0
        self._checked = 0.0
        self.pinned: dict[tuple[str, str], float] = {}
        self.recent: dict[tuple, float] = {}
        self.queue: deque[dict] = deque(maxlen=5000)
        self._lock = threading.Lock()
        threading.Thread(target=self._ship_loop, daemon=True, name="bw-ship").start()

    # ---------------------------------------------------------------- state
    def _refresh(self) -> None:
        now = time.monotonic()
        if now - self._checked < 1:
            return
        self._checked = now
        try:
            mtime = STATE_FILE.stat().st_mtime
            if mtime == self._mtime:
                return
            state = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            return
        self._mtime = mtime
        self.by_ip = {d["ip"]: d for d in state.get("devices", []) if d.get("ip")}
        self.passthrough = frozenset(state.get("passthrough", []))
        inspecting = {d["mac"] for d in self.by_ip.values() if d.get("inspect")}
        self.pinned = {k: v for k, v in self.pinned.items() if k[0] in inspecting}

    def _device(self, peer) -> dict | None:
        self._refresh()
        ip = (peer[0] if peer else "").removeprefix("::ffff:")
        return self.by_ip.get(ip)

    # --------------------------------------------------------------- events
    def _emit(self, dev: dict, kind: str, host: str, dedupe: tuple | None = None, window: float = 60, **data) -> None:
        now = time.time()
        if dedupe is not None:
            key = (dev["mac"], kind, *dedupe)
            if now - self.recent.get(key, 0) < window:
                return
            self.recent[key] = now
            if len(self.recent) > 20000:
                self.recent = {k: v for k, v in self.recent.items() if now - v < 3600}
        with self._lock:
            self.queue.append({"t": now, "mac": dev["mac"], "k": kind, "host": host, **data})

    def _ship_loop(self) -> None:
        while True:
            time.sleep(2)
            with self._lock:
                items = list(self.queue)
                self.queue.clear()
            if not items:
                continue
            body = json.dumps({"facts": [{"kind": "web", "data": {"items": items}}]}).encode()
            req = urllib.request.Request(f"{CORE_URL}/internal/report", data=body, method="POST",
                                         headers={"Content-Type": "application/json", "X-BW-Token": TOKEN})
            try:
                urllib.request.urlopen(req, timeout=5).read()
            except Exception:
                with self._lock:  # core is briefly away: keep the batch for the next round
                    self.queue.extendleft(reversed(items))

    # ------------------------------------------------------------------ TLS
    def tls_clienthello(self, data: tls.ClientHelloData) -> None:
        dev = self._device(data.context.client.peername)
        sni = (data.client_hello.sni or "").lower()
        if dev is None or not dev.get("inspect") or not sni or extract.is_passthrough(sni, self.passthrough):
            data.ignore_connection = True
            return
        if time.time() - self.pinned.get((dev["mac"], sni), 0) < PIN_TTL:
            data.ignore_connection = True

    def tls_failed_client(self, data: tls.TlsData) -> None:
        """The client refused our certificate (pinning, or the CA isn't installed): let that host through next time."""
        dev = self._device(data.context.client.peername)
        sni = (data.conn.sni or "").lower()
        if dev is None or not sni:
            return
        self.pinned[(dev["mac"], sni)] = time.time()
        self._emit(dev, "pinned", sni, dedupe=(sni,), window=PIN_TTL)

    # ----------------------------------------------------------------- HTTP
    def request(self, flow: http.HTTPFlow) -> None:
        dev = self._device(flow.client_conn.peername)
        if dev is None or not dev.get("inspect"):
            return
        req = flow.request
        host = req.pretty_host.lower()
        parts = urlsplit(req.pretty_url)

        hit = extract.keyword_hit(dev.get("keywords") or [], host, parts.path, parts.query)
        if hit:
            self._emit(dev, "block", host, dedupe=(host, hit), keyword=hit, url=extract.clean_url(req.pretty_url))
            flow.response = http.Response.make(
                403, BLOCK_HTML.format(reason=f"It matches a word your family has blocked: “{escape(hit)}”.").encode(),
                {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"})
            return

        found = extract.search_query(host, parts.path, parts.query)
        if not found and req.method == "POST" and host.endswith(("youtube.com", "googleapis.com")):
            q = extract.youtube_search_body(parts.path, req.raw_content or b"")
            found = ("YouTube", q) if q else None
        if found:
            self._emit(dev, "search", host, dedupe=(found[1].lower(),), window=120, engine=found[0], q=found[1])

        if dev.get("safesearch"):
            rewritten = extract.enforce_safesearch(host, req.pretty_url)
            if rewritten:
                req.url = rewritten
        if dev.get("youtube") in ("moderate", "strict") and (
                host.endswith(("youtube.com", "youtube-nocookie.com")) or host == "youtubei.googleapis.com"):
            req.headers["YouTube-Restrict"] = "Strict" if dev["youtube"] == "strict" else "Moderate"

        navigation = (req.method == "GET" and req.headers.get("sec-fetch-dest") == "document"
                      and req.headers.get("sec-fetch-mode") == "navigate")
        flow.metadata["bw_nav"] = navigation
        flow.metadata["bw_player"] = host.endswith(("youtube.com", "googleapis.com")) and \
            parts.path.startswith("/youtubei/v1/player")
        vid = extract.youtube_video_id(host, parts.path, parts.query)
        if vid and navigation:
            flow.metadata["bw_vid"] = vid

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        """Stream everything except the few small responses we read a title from."""
        resp = flow.response
        wanted = flow.metadata.get("bw_player") or (
            flow.metadata.get("bw_nav") and "html" in resp.headers.get("content-type", ""))
        size = int(resp.headers.get("content-length") or 0)
        if not wanted or size > MAX_BUFFER or resp.status_code != 200:
            resp.stream = True

    def response(self, flow: http.HTTPFlow) -> None:
        dev = self._device(flow.client_conn.peername)
        resp = flow.response
        if dev is None or not dev.get("inspect") or resp is None or resp.stream or resp.status_code != 200:
            return
        host = flow.request.pretty_host.lower()
        if flow.metadata.get("bw_player"):
            details = extract.youtube_player_details(resp.content or b"")
            if details:
                self._emit(dev, "video", host, dedupe=(details["vid"],), window=600, **details)
            return
        if not flow.metadata.get("bw_nav"):
            return
        html = resp.get_text(strict=False) or ""
        title = extract.page_title(html)
        vid = flow.metadata.get("bw_vid")
        if vid:
            channel, dur = extract.youtube_page_details(html)
            self._emit(dev, "video", host, dedupe=(vid,), window=600, vid=vid,
                       title=title.removesuffix(" - YouTube"), channel=channel, dur=dur)
        url = extract.clean_url(flow.request.pretty_url)
        self._emit(dev, "visit", host, dedupe=(url,), window=30, url=url, title=title)


addons = [Breakwater()]
