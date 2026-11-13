"""Pure helpers for the inspecting proxy: what a request tells us, and how to make it safer.

Kept free of mitmproxy imports so it can be unit-tested anywhere.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, unquote_plus, urlencode, urlsplit, urlunsplit

# host suffix -> (engine name, path prefix, query parameter)
SEARCH_ENGINES = [
    ("google.", "Google", "/search", "q"),
    ("bing.com", "Bing", "/search", "q"),
    ("duckduckgo.com", "DuckDuckGo", "/", "q"),
    ("search.yahoo.com", "Yahoo", "/search", "p"),
    ("search.brave.com", "Brave", "/search", "q"),
    ("ecosia.org", "Ecosia", "/search", "q"),
    ("youtube.com", "YouTube", "/results", "search_query"),
    ("startpage.com", "Startpage", "/sp/search", "query"),
    ("yandex.com", "Yandex", "/search", "text"),
]
_GOOGLE_HOST = re.compile(r"(^|\.)google\.[a-z.]{2,6}$")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&nbsp;": " "}


def _host_matches(host: str, suffix: str) -> bool:
    if suffix == "google.":
        return bool(_GOOGLE_HOST.search(host))
    return host == suffix or host.endswith("." + suffix)


def search_query(host: str, path: str, query: str) -> tuple[str, str] | None:
    """(engine, query text) if this request is a search-results page."""
    host = host.lower()
    for suffix, engine, prefix, param in SEARCH_ENGINES:
        if not _host_matches(host, suffix):
            continue
        if prefix == "/" and path not in ("/", "/html", "/html/", "/lite", "/lite/"):
            continue
        if prefix != "/" and not path.startswith(prefix):
            continue
        values = parse_qs(query).get(param)
        if values and values[0].strip():
            return engine, " ".join(values[0].split())[:300]
    return None


def youtube_search_body(path: str, body: bytes) -> str | None:
    """The YouTube app-style API carries the query in a JSON body."""
    if not path.startswith("/youtubei/v1/search"):
        return None
    try:
        q = json.loads(body).get("query")
    except Exception:
        return None
    return " ".join(q.split())[:300] if isinstance(q, str) and q.strip() else None


def youtube_video_id(host: str, path: str, query: str) -> str | None:
    host = host.lower()
    if host.endswith("youtu.be"):
        vid = path.strip("/")
    elif host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        if path == "/watch":
            vid = (parse_qs(query).get("v") or [""])[0]
        elif path.startswith(("/shorts/", "/embed/", "/live/")):
            vid = path.split("/")[2] if len(path.split("/")) > 2 else ""
        else:
            return None
    else:
        return None
    return vid if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", vid or "") else None


def youtube_player_details(body: bytes) -> dict | None:
    """Pull title/channel/length out of a /youtubei/v1/player response."""
    try:
        d = json.loads(body).get("videoDetails") or {}
    except Exception:
        return None
    if not d.get("videoId"):
        return None
    return {"vid": d["videoId"], "title": (d.get("title") or "")[:200], "channel": (d.get("author") or "")[:100],
            "dur": int(d.get("lengthSeconds") or 0)}


def youtube_page_details(html: str) -> tuple[str, int]:
    """(channel, length in seconds) from the player data embedded in a watch page; blanks if absent."""
    start = html.find('"videoDetails"')
    if start < 0:
        return "", 0
    chunk = html[start:start + 6000]
    author = re.search(r'"author":"((?:[^"\\]|\\.)*)"', chunk)
    length = re.search(r'"lengthSeconds":"(\d+)"', chunk)
    channel = author.group(1) if author else ""
    try:
        channel = json.loads(f'"{channel}"')
    except ValueError:
        pass
    return channel[:100], int(length.group(1)) if length else 0


def page_title(html: str) -> str:
    m = _TITLE.search(html[:200_000])
    if not m:
        return ""
    title = " ".join(m.group(1).split())
    for ent, ch in _ENTITIES.items():
        title = title.replace(ent, ch)
    return title[:200]


def keyword_hit(keywords: list[str], host: str, path: str, query: str) -> str | None:
    """First parent-defined keyword found in the address (whole words, case-insensitive)."""
    if not keywords:
        return None
    text = unquote_plus(f"{host}{path}?{query}").lower()
    for kw in keywords:
        if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text):
            return kw
    return None


def enforce_safesearch(host: str, url: str) -> str | None:
    """Return a rewritten URL with the engine's strict flag forced on, or None if nothing to change."""
    host = host.lower()
    parts = urlsplit(url)
    params = parse_qs(parts.query, keep_blank_values=True)
    want: dict[str, str] = {}
    if _GOOGLE_HOST.search(host) and parts.path.startswith(("/search", "/images")):
        want = {"safe": "active"}
    elif _host_matches(host, "bing.com") and parts.path.startswith(("/search", "/images", "/videos")):
        want = {"adlt": "strict"}
    elif _host_matches(host, "duckduckgo.com") and "q" in params:
        want = {"kp": "1"}
    elif _host_matches(host, "search.yahoo.com"):
        want = {"vm": "r"}
    if not want or all(params.get(k) == [v] for k, v in want.items()):
        return None
    for k, v in want.items():
        params[k] = [v]
    return urlunsplit(parts._replace(query=urlencode(params, doseq=True)))


def clean_url(url: str, limit: int = 400) -> str:
    """Keep addresses readable and bounded for storage."""
    return url if len(url) <= limit else url[:limit] + "…"


def is_passthrough(host: str, suffixes: frozenset[str]) -> bool:
    host = host.lower().rstrip(".")
    parts = host.split(".")
    return any(".".join(parts[i:]) in suffixes for i in range(len(parts)))
