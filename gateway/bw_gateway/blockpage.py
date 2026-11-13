"""The page a paused device sees. Served on a local port that the firewall redirects plain HTTP to."""
from __future__ import annotations

import json
import threading
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Internet paused</title>
<style>
body{{font:17px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;background:#0d2530;color:#e8f1f2;display:grid;
place-items:center;min-height:100vh;margin:0}}
main{{max-width:26rem;padding:2rem;text-align:center}}
svg{{width:5rem;height:5rem;margin-bottom:1rem}}
h1{{font-size:1.6rem;margin:0 0 .4rem;font-weight:650}}
p{{color:#a9c1c7;margin:.3rem 0}} strong{{color:#e8f1f2}}
</style>
<main>
<svg viewBox="0 0 64 64" fill="none" stroke="#6fd0c4" stroke-width="3" stroke-linecap="round" aria-hidden="true">
<path d="M6 44c6-5 12-5 18 0s12 5 18 0 12-5 16 0"/><path d="M6 54c6-5 12-5 18 0s12 5 18 0 12-5 16 0"/>
<path d="M32 8v22M22 18h20"/><circle cx="32" cy="8" r="3"/></svg>
<h1>{title}</h1><p>{line}</p><p>{until}</p>
</main></html>"""

REASONS = {
    "bedtime": ("It's bedtime", "The internet is resting too."),
    "limit": ("Time's up for today", "You've used today's screen time."),
    "manual": ("Internet is paused", "A parent has paused this device."),
}


def render(device: dict | None, tz_name: str) -> str:
    reason = (device or {}).get("lock_reason") or "manual"
    title, line = REASONS.get(reason, REASONS["manual"])
    until = ""
    ts = (device or {}).get("lock_until") or 0
    if ts:
        try:
            when = datetime.fromtimestamp(ts, ZoneInfo(tz_name))
            until = f"Back at <strong>{escape(when.strftime('%-I:%M %p'))}</strong>."
        except Exception:
            until = ""
    return PAGE.format(title=escape(title), line=escape(line), until=until)


def serve(port: int, state_file: Path) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            device, tz = None, "UTC"
            try:
                state = json.loads(state_file.read_text())
                tz = state.get("tz", "UTC")
                device = next((d for d in state["devices"] if d.get("ip") == self.client_address[0]), None)
            except Exception:
                pass
            body = render(device, tz).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        do_HEAD = do_POST = do_GET  # noqa: N815

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="bw-blockpage").start()
    return server
