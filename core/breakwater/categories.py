"""Turn a hostname into a *site* (a known app, or a registrable domain) and a category.

Known apps come from AdGuard Home's blockable-services catalogue (a snapshot is
bundled so this works offline); a short curated list fills in learning sites,
casual-game sites and background "system" chatter that should never count as
screen time.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import tldextract

_extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

CATEGORIES = ["streaming", "social", "games", "chat", "learning", "shopping", "ai", "other"]
CATEGORY_LABELS = {
    "streaming": "Video & music", "social": "Social", "games": "Games", "chat": "Chat",
    "learning": "Learning", "shopping": "Shopping", "ai": "AI", "other": "Browsing", "system": "Background",
}

GROUP_CATEGORY = {
    "streaming": "streaming", "social_network": "social", "gaming": "games", "messenger": "chat",
    "shopping": "shopping", "ai": "ai", "gambling": "other", "dating": "other", "hosting": "other",
    "privacy": "other", "software": "system", "cdn": "system",
}

LEARNING = """khanacademy.org wikipedia.org wikimedia.org duolingo.com scratch.mit.edu code.org quizlet.com
desmos.com brainpop.com ixl.com prodigygame.com kahoot.it kahoot.com nationalgeographic.com britannica.com
wolframalpha.com coursera.org edx.org mathway.com typing.com blooket.com schoology.com instructure.com
classroom.google.com docs.google.com drive.google.com sites.google.com scholar.google.com office.com
sharepoint.com onenote.com readtheory.org epic.com getepic.com mathletics.com raz-kids.com learninga-z.com
tinkercad.com geogebra.org nasa.gov pbskids.org cbc.ca commonlit.org newsela.com seesaw.me freshgrade.com
myon.com starfall.com abcya.com gov.bc.ca""".split()

GAMES = """poki.com coolmathgames.com crazygames.com friv.com chess.com lichess.org itch.io supercell.com
innersloth.com mojang.com miniclip.com y8.com kongregate.com armorgames.com addictinggames.com 1v1.lol
krunker.io slither.io agar.io unity3d.com unity.com gamepix.com now.gg""".split()

SYSTEM = """gstatic.com googleapis.com googleusercontent.com gvt1.com gvt2.com gvt3.com windowsupdate.com
msftconnecttest.com msftncsi.com windows.com windows.net microsoft.com live.com msn.com office.net msedge.net
azureedge.net azure.com trafficmanager.net akamai.net akamaiedge.net akamaized.net akamaihd.net edgekey.net
edgesuite.net cloudfront.net amazonaws.com cloudflare.com cloudflare.net cloudflare-dns.com fastly.net
fastlylb.net doubleclick.net googlesyndication.com googleadservices.com google-analytics.com
googletagmanager.com googletagservices.com app-measurement.com crashlytics.com firebaseio.com
firebaseinstallations.googleapis.com digicert.com lencr.org letsencrypt.org pki.goog ntp.org android.com
apple.com icloud.com aaplimg.com mzstatic.com mozilla.com mozilla.net mozilla.org firefox.com ubuntu.com
debian.org fedoraproject.org samsung.com samsungcloud.com samsungqbe.com xiaomi.com miui.com in-addr.arpa
ip6.arpa home.arpa adguard.com adguard-dns.com sentry.io newrelic.com nr-data.net scorecardresearch.com
branch.io appsflyer.com adjust.com onesignal.com bugsnag.com segment.io amplitude.com mixpanel.com
optimizely.com cookielaw.org onetrust.com typekit.net fontawesome.com jsdelivr.net unpkg.com cdnjs.com
jquery.com bootstrapcdn.com googlezip.net 1e100.net dns.google one.one.one.one quad9.net opendns.com
connectivitycheck.gstatic.com clients.google.com accounts.google.com play.googleapis.com""".split()

_LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal", ".arpa", ".localdomain")


class Catalogue:
    def __init__(self, raw: dict[str, Any]):
        self.services: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, list[str]] = {}
        self._by_domain: dict[str, str] = {}
        for s in raw.get("blocked_services", []):
            sid = s["id"]
            self.services[sid] = {"id": sid, "name": s["name"], "group": s.get("group_id", ""),
                                  "icon": s.get("icon_svg", "")}
            self.groups.setdefault(s.get("group_id", ""), []).append(sid)
            for rule in s.get("rules", []):
                if rule.startswith("||") and rule.endswith("^"):
                    dom = rule[2:-1].lower()
                    if "*" not in dom and "/" not in dom:
                        self._by_domain.setdefault(dom, sid)
        self._curated: dict[str, str] = {}
        for dom in LEARNING:
            self._curated[dom] = "learning"
        for dom in GAMES:
            self._curated[dom] = "games"
        for dom in SYSTEM:
            self._curated[dom] = "system"

    @staticmethod
    def _suffixes(host: str):
        parts = host.split(".")
        for i in range(len(parts)):
            yield ".".join(parts[i:])

    def classify(self, host: str) -> tuple[str, str]:
        """Return (site, category). `site` is '@service_id' for a known app, else the registrable domain."""
        host = host.lower().rstrip(".")
        if not host or "." not in host or host.endswith(_LOCAL_SUFFIXES):
            return host or "local", "system"
        # Curated host-level entries win over service matches (docs.google.com is school, not "Google").
        for suf in self._suffixes(host):
            if suf in self._curated and suf.count(".") >= 2:
                return self.registrable(suf) if self._curated[suf] == "system" else suf, self._curated[suf]
        for suf in self._suffixes(host):
            sid = self._by_domain.get(suf)
            if sid:
                grp = self.services[sid]["group"]
                cat = "streaming" if grp == "streaming" else GROUP_CATEGORY.get(grp, "other")
                return "@" + sid, cat
        reg = self.registrable(host)
        return reg, self._curated.get(reg, "other")

    @staticmethod
    def registrable(host: str) -> str:
        ext = _extract(host)
        return getattr(ext, "top_domain_under_public_suffix", None) or ext.registered_domain or host

    def label(self, site: str) -> str:
        if site.startswith("@"):
            svc = self.services.get(site[1:])
            return svc["name"] if svc else site[1:]
        return site

    def public(self) -> dict[str, Any]:
        return {"services": list(self.services.values()),
                "groups": [{"id": g, "services": ids} for g, ids in sorted(self.groups.items()) if g]}


@lru_cache(maxsize=1)
def catalogue() -> Catalogue:
    raw = json.loads((Path(__file__).parent / "data" / "services.json").read_text())
    return Catalogue(raw)
