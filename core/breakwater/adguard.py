"""Everything that talks to the two AdGuard Home instances.

* `bootstrap_yaml` writes the first-run config (so nobody clicks through a setup wizard).
* `Instance.sync` makes an instance's per-device clients and custom rules match Breakwater's policy.
* `Instance.pull_log` reads new query-log entries for ingestion.
"""
from __future__ import annotations

import logging
from typing import Any

import bcrypt
import httpx
import yaml

log = logging.getLogger("bw.adguard")

_HAGEZI = "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/adblock/"

# (id, name, url, reason label used in the dashboard)
STRICT_LISTS = [
    (101, "Adult content (HaGeZi NSFW)", _HAGEZI + "nsfw.txt", "adult"),
    (103, "VPN, proxy and DNS bypass (HaGeZi)", _HAGEZI + "doh-vpn-proxy-bypass.txt", "bypass"),
    (104, "Gambling (HaGeZi)", _HAGEZI + "gambling.medium.txt", "gambling"),
    (105, "Malware and phishing (HaGeZi TIF)", _HAGEZI + "tif.medium.txt", "malware"),
    (106, "Ads and trackers (HaGeZi Pro)", _HAGEZI + "pro.txt", "ads"),
]
BASIC_LISTS = [
    (201, "Ads and trackers (HaGeZi Pro)", _HAGEZI + "pro.txt", "ads"),
    (202, "Malware and phishing (HaGeZi TIF)", _HAGEZI + "tif.medium.txt", "malware"),
]
LIST_REASON = {i: reason for i, _, _, reason in STRICT_LISTS + BASIC_LISTS}

YOUTUBE_HOSTS = ["www.youtube.com", "m.youtube.com", "youtubei.googleapis.com", "youtube.googleapis.com",
                 "www.youtube-nocookie.com"]
CLIENT_PREFIX = "bw-"


def bootstrap_yaml(tier: str, web_port: int, dns_port: int, user: str, password: str) -> str:
    """Initial AdGuardHome.yaml for one instance. AdGuard fills in every key we leave out."""
    strict = tier == "strict"
    lists = STRICT_LISTS if strict else BASIC_LISTS
    conf: dict[str, Any] = {
        "http": {"address": f"127.0.0.1:{web_port}", "session_ttl": "720h"},
        "users": [{"name": user, "password": bcrypt.hashpw(password.encode(), bcrypt.gensalt(10)).decode()}],
        "dns": {
            "bind_hosts": ["0.0.0.0"],
            "port": dns_port,
            "ratelimit": 0,
            "refuse_any": True,
            # Strict resolves through Cloudflare for Families (adult + malware filtered upstream) as a second net.
            "upstream_dns": (["https://family.cloudflare-dns.com/dns-query"] if strict
                             else ["https://security.cloudflare-dns.com/dns-query", "https://dns.quad9.net/dns-query"]),
            "bootstrap_dns": ["1.1.1.1", "9.9.9.9"],
            "fallback_dns": ["https://dns.quad9.net/dns-query"],
            "upstream_mode": "load_balance",
            "cache_enabled": True,
            "cache_size": 8388608,
            "enable_dnssec": False,
            "handle_ddr": False,  # never advertise encrypted DNS that would skip per-device policy
            "anonymize_client_ip": False,
        },
        "querylog": {"enabled": True, "file_enabled": True, "interval": "24h", "size_memory": 200},
        "statistics": {"enabled": True, "interval": "168h"},
        "filters": [{"enabled": True, "url": url, "name": name, "id": i} for i, name, url, _ in lists],
        "filtering": {
            "filtering_enabled": True,
            "protection_enabled": True,
            "parental_enabled": strict,
            "safebrowsing_enabled": True,
            "safe_search": {"enabled": strict, "bing": True, "duckduckgo": True, "ecosia": True, "google": True,
                            "pixabay": True, "yandex": True, "youtube": strict},
            "blocked_services": {"schedule": {"time_zone": "Local"}, "ids": []},
            "filters_update_interval": 12,
            "blocking_mode": "default",
            "blocked_response_ttl": 10,
        },
        "clients": {"runtime_sources": {"whois": False, "arp": True, "rdns": False, "dhcp": False, "hosts": False},
                    "persistent": []},
        "schema_version": 34,
    }
    return yaml.safe_dump(conf, sort_keys=False)


class Instance:
    def __init__(self, tier: str, url: str, user: str, password: str):
        self.tier = tier
        self.http = httpx.Client(base_url=url.rstrip("/") + "/control", auth=(user, password), timeout=15)
        self._rules: list[str] | None = None
        self._filters_checked = False
        self.last_error = ""
        self.last_ok = 0.0

    # ---------------------------------------------------------------- basics
    def get(self, path: str, **params: Any) -> Any:
        r = self.http.get(path, params=params or None)
        r.raise_for_status()
        return r.json() if r.content else None

    def post(self, path: str, body: Any = None, method: str = "POST") -> Any:
        r = self.http.request(method, path, json=body)
        if r.status_code >= 400:
            raise httpx.HTTPStatusError(f"{path}: {r.status_code} {r.text[:200]}", request=r.request, response=r)
        return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else None

    def healthy(self) -> bool:
        try:
            return bool(self.get("/status").get("running"))
        except Exception:
            return False

    def services(self) -> dict[str, Any]:
        return self.get("/blocked_services/all")

    def filter_status(self) -> list[dict[str, Any]]:
        return self.get("/filtering/status").get("filters") or []

    def ensure_filters(self) -> None:
        """Add any list this version of Breakwater ships that the instance doesn't have yet."""
        if self._filters_checked:
            return
        have = {f["url"] for f in self.filter_status()}
        for _, name, url, _ in (STRICT_LISTS if self.tier == "strict" else BASIC_LISTS):
            if url not in have:
                self.post("/filtering/add_url", {"name": name, "url": url, "whitelist": False})
        self._filters_checked = True

    def list_reasons(self) -> dict[int, str]:
        """Map this instance's filter ids to dashboard reasons (ids differ if lists were added via the API)."""
        by_url = {url: reason for _, _, url, reason in STRICT_LISTS + BASIC_LISTS}
        return {f["id"]: by_url.get(f["url"], "custom") for f in self.filter_status()}

    # ------------------------------------------------------------------ sync
    def sync(self, devices: list[dict[str, Any]]) -> None:
        """`devices`: [{mac, ip, eff: Effective-as-dict, soft_lock: bool}] that belong on this instance."""
        self.ensure_filters()
        want: dict[str, dict[str, Any]] = {}
        rules: list[str] = []
        blocks: dict[str, list[str]] = {}
        allows: dict[str, list[str]] = {}
        for d in devices:
            eff, ip = d["eff"], d["ip"]
            filtered = eff["tier"] != "none"
            name = CLIENT_PREFIX + d["mac"]
            want[name] = {
                "name": name, "ids": [ip], "use_global_settings": False,
                "filtering_enabled": filtered,
                "parental_enabled": eff["tier"] == "strict",
                "safebrowsing_enabled": filtered,
                "safe_search": {"enabled": bool(eff["safesearch"]) and filtered, "bing": True, "duckduckgo": True,
                                "ecosia": True, "google": True, "pixabay": True, "yandex": True,
                                "youtube": eff["youtube"] == "moderate"},
                "use_global_blocked_services": False,
                "blocked_services": eff["blocked_services"] if filtered else [],
                "upstreams": [], "tags": [],
                "ignore_querylog": eff["monitoring"] == "off",
                "ignore_statistics": eff["monitoring"] == "off",
            }
            if not filtered:
                continue
            for dom in eff["block_domains"]:
                blocks.setdefault(dom, []).append(ip)
            for dom in eff["allow_domains"]:
                allows.setdefault(dom, []).append(ip)
            if eff["youtube"] == "strict":
                rules += [f"|{h}^$dnsrewrite=NOERROR;CNAME;restrict.youtube.com,client={ip}" for h in YOUTUBE_HOSTS]
            if d.get("soft_lock"):
                rules.append(f"/.*/$important,client={ip}")
        rules += [f"||{dom}^$client={'|'.join(sorted(ips))}" for dom, ips in sorted(blocks.items())]
        rules += [f"@@||{dom}^$important,client={'|'.join(sorted(ips))}" for dom, ips in sorted(allows.items())]

        have = {c["name"]: c for c in (self.get("/clients").get("clients") or []) if c["name"].startswith(CLIENT_PREFIX)}
        # Remove first: an IP can only belong to one client, and devices change address or instance.
        stale = [n for n, cur in have.items() if n not in want or cur.get("ids") != want[n]["ids"]]
        for name in stale:
            self.post("/clients/delete", {"name": name})
            del have[name]
        for name, client in want.items():
            cur = have.get(name)
            if cur is None:
                self.post("/clients/add", client)
            elif any(_norm(cur.get(k)) != _norm(v) for k, v in client.items()):
                self.post("/clients/update", {"name": name, "data": client})
        if rules != self._rules:
            current = self.get("/filtering/status").get("user_rules") or []
            foreign = [r for r in current if not _ours(r)]
            self.post("/filtering/set_rules", {"rules": foreign + rules})
            self._rules = rules

    # ------------------------------------------------------------- query log
    def pull_log(self, cursor: float, max_pages: int = 20) -> tuple[list[dict[str, Any]], float]:
        """Entries newer than `cursor` (epoch seconds), oldest first, plus the new cursor. Adds `ts` to each."""
        out: list[dict[str, Any]] = []
        older_than = ""
        for _ in range(max_pages):
            params: dict[str, Any] = {"limit": 500}
            if older_than:
                params["older_than"] = older_than
            page = self.get("/querylog", **params)
            data = page.get("data") or []
            done = False
            for item in data:
                item["ts"] = parse_time(item["time"])
                if item["ts"] <= cursor:
                    done = True
                    break
                out.append(item)
            if done or not data or not page.get("oldest"):
                break
            older_than = page["oldest"]
            if not cursor:  # first run: one page of history is plenty
                break
        out.reverse()
        return out, (out[-1]["ts"] if out else cursor)


def parse_time(s: str) -> float:
    """RFC 3339 with up to nanosecond precision (which datetime.fromisoformat rejects) to epoch seconds."""
    from datetime import datetime
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        cut = next((i for i, ch in enumerate(rest) if ch in "+-"), len(rest))
        frac, zone = rest[:cut], rest[cut:]
        s = f"{head}.{(frac + '000000')[:6]}{zone}"
    return datetime.fromisoformat(s).timestamp()


def _ours(rule: str) -> bool:
    return "client=" in rule and (rule.startswith(("||", "@@||", "/.*/")) or "$dnsrewrite=" in rule)


def _norm(v: Any) -> Any:
    if isinstance(v, list):
        return sorted(v, key=str)
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v if v is not None else []
