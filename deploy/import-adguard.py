#!/usr/bin/env python3
"""Carry settings over from an existing AdGuard Home into Breakwater's two managed filters.

Run on the primary node after `docker compose up -d`:

    python3 deploy/import-adguard.py http://192.168.0.10:3000

It copies, and never deletes anything on the old instance:
  * custom filtering rules  -> both Breakwater filters (rules aimed at one client are skipped; set those per person)
  * DNS rewrites            -> both filters
  * extra blocklists        -> the Basic filter, and the Family filter too
  * client names            -> printed as a table so you can name devices in the dashboard

Only the Python standard library is used.
"""
from __future__ import annotations

import base64
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    path = Path(__file__).resolve().parent.parent / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.split("#")[0].strip())
    return env


class AdGuard:
    def __init__(self, base: str, user: str, password: str):
        self.base = base.rstrip("/") + "/control"
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def call(self, path: str, body=None, method: str | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method or ("POST" if data else "GET"),
                                     headers={"Authorization": self.auth, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw and raw.strip().startswith((b"{", b"[")) else None


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    env = load_env()
    old = AdGuard(sys.argv[1], input("Existing AdGuard Home username: "), getpass.getpass("Existing AdGuard Home password: "))
    try:
        status = old.call("/filtering/status")
    except urllib.error.HTTPError as exc:
        print(f"Could not sign in to the existing AdGuard Home ({exc.code}).")
        return 1
    targets = {
        "Family": AdGuard("http://127.0.0.1:3001", env.get("BW_ADGUARD_USER", "breakwater"), env["BW_ADGUARD_PASSWORD"]),
        "Basic": AdGuard("http://127.0.0.1:3002", env.get("BW_ADGUARD_USER", "breakwater"), env["BW_ADGUARD_PASSWORD"]),
    }

    rules = [r for r in status.get("user_rules") or [] if r.strip() and not r.lstrip().startswith(("!", "#"))]
    general = [r for r in rules if "client=" not in r and "ctag=" not in r]
    skipped = len(rules) - len(general)
    rewrites = old.call("/rewrite/list") or []
    lists = [f for f in status.get("filters") or [] if f.get("enabled")]

    for name, target in targets.items():
        current = target.call("/filtering/status")
        have_rules = current.get("user_rules") or []
        merged = have_rules + [r for r in general if r not in have_rules]
        target.call("/filtering/set_rules", {"rules": merged})
        have_rw = {(r["domain"], r["answer"]) for r in target.call("/rewrite/list") or []}
        added_rw = 0
        for rw in rewrites:
            if (rw["domain"], rw["answer"]) not in have_rw:
                target.call("/rewrite/add", {"domain": rw["domain"], "answer": rw["answer"]})
                added_rw += 1
        have_urls = {f["url"] for f in current.get("filters") or []}
        added_lists = 0
        for f in lists:
            if f["url"] not in have_urls:
                try:
                    target.call("/filtering/add_url", {"name": f["name"], "url": f["url"], "whitelist": False})
                    added_lists += 1
                except urllib.error.HTTPError as exc:
                    print(f"  {name}: could not add list {f['name']} ({exc.code})")
        print(f"{name} filter: {len(merged) - len(have_rules)} rules, {added_rw} rewrites, {added_lists} blocklists added")

    if skipped:
        print(f"\n{skipped} rule(s) were aimed at specific clients and were not copied. "
              "Recreate them under People > (person) > Sites.")
    clients = (old.call("/clients") or {}).get("clients") or []
    if clients:
        print("\nNamed clients on the old instance (name the matching devices under Devices):")
        for c in clients:
            print(f"  {c['name']:<28} {', '.join(c.get('ids') or [])}")
    print("\nDone. The old AdGuard Home was not changed. Retire it once devices are using Breakwater.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
