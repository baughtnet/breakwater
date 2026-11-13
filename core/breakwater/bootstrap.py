"""One-shot init, run before the AdGuard containers start: write their first-run config if it is missing.

    python -m breakwater.bootstrap /adguard/strict /adguard/basic
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .adguard import bootstrap_yaml


def main(argv: list[str]) -> int:
    user = os.environ.get("BW_ADGUARD_USER", "breakwater")
    password = os.environ.get("BW_ADGUARD_PASSWORD", "")
    if len(password) < 12:
        print("BW_ADGUARD_PASSWORD must be set (12+ characters). Generate one with: openssl rand -hex 16", file=sys.stderr)
        return 1
    plan = [("strict", argv[0], 3001, 5301), ("basic", argv[1], 3002, 5302)]
    for tier, conf_dir, web_port, dns_port in plan:
        target = Path(conf_dir) / "AdGuardHome.yaml"
        if target.exists():
            print(f"{tier}: config already present, leaving it alone")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(bootstrap_yaml(tier, web_port, dns_port, user, password))
        os.chmod(target, 0o600)
        print(f"{tier}: wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
