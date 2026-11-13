"""Search-term watch lists. A match raises an alert for a parent to look at; it never blocks by itself.

The lists are deliberately short and phrase-based to keep false alarms rare.
Parents can add their own terms in Settings (stored under the `watch_terms` setting).
"""
from __future__ import annotations

import re
from functools import lru_cache

WATCH: dict[str, tuple[str, list[str]]] = {
    # category: (severity, phrases)
    "self_harm": ("high", [
        "kill myself", "want to die", "suicide", "suicidal", "end my life", "self harm", "self-harm",
        "cut myself", "cutting myself", "how to overdose", "painless death", "hang myself", "hate my life",
        "nobody would miss me", "ways to die",
    ]),
    "explicit": ("medium", [
        "porn", "porno", "pornhub", "xvideos", "xnxx", "hentai", "nude", "nudes", "naked girls", "naked boys",
        "sex video", "sex videos", "xxx", "onlyfans", "rule 34", "rule34", "nsfw", "milf", "blowjob", "boobs",
    ]),
    "drugs": ("medium", [
        "buy weed", "buy vape", "vape without parents", "how to get high", "get drunk", "buy alcohol underage",
        "fake id", "shrooms", "lsd", "cocaine", "mdma", "xanax", "percocet", "how to roll a joint", "dab pen",
        "nicotine pouches", "zyn",
    ]),
    "violence": ("high", [
        "how to make a bomb", "school shooting", "shoot up", "how to make a gun", "ghost gun", "buy a gun",
        "how to hurt someone", "how to kill", "gore video", "beheading",
    ]),
    "bypass": ("medium", [
        "free vpn", "unblock proxy", "bypass parental controls", "bypass adguard", "bypass family link",
        "unblocked games", "how to get around wifi block", "proxy site", "tor browser", "change dns",
        "get past school filter", "how to bypass",
    ]),
    "predation": ("high", [
        "send nudes", "meet strangers", "omegle", "sugar daddy", "chat with strangers", "is it ok to send pictures",
    ]),
    "disordered_eating": ("high", [
        "pro ana", "proana", "thinspo", "how to starve", "how to purge", "lose weight fast for kids",
        "how to make yourself throw up",
    ]),
}

CATEGORY_TITLES = {
    "self_harm": "Search suggests self-harm",
    "explicit": "Explicit search",
    "drugs": "Search about drugs or alcohol",
    "violence": "Search about violence or weapons",
    "bypass": "Search about getting around the filter",
    "predation": "Search suggests risky contact",
    "disordered_eating": "Search suggests disordered eating",
    "custom": "Search matched one of your watch terms",
}


def _pattern(phrases: list[str]) -> re.Pattern[str]:
    alts = sorted((re.escape(p.lower()).replace(r"\ ", r"\s+") for p in phrases), key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(alts) + r")(?![a-z0-9])")


@lru_cache(maxsize=1)
def _builtin() -> list[tuple[str, str, re.Pattern[str]]]:
    return [(cat, sev, _pattern(phrases)) for cat, (sev, phrases) in WATCH.items()]


def check(text: str, custom: list[str] | None = None) -> tuple[str, str, str] | None:
    """Return (category, severity, matched phrase) for the first watch list the text hits."""
    t = " ".join(text.lower().split())
    for cat, sev, pat in _builtin():
        m = pat.search(t)
        if m:
            return cat, sev, m.group(0)
    terms = [c for c in (custom or []) if c.strip()]
    if terms:
        m = _pattern(terms).search(t)
        if m:
            return "custom", "medium", m.group(0)
    return None
