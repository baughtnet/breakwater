"""Entry point: `python -m breakwater.main` runs one node's core service."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import uvicorn

from .api import create_app
from .config import cfg
from .engine import Engine


def ensure_defaults(eng: Engine) -> None:
    """A fresh primary gets one catch-all profile for shared things like printers and TVs."""
    if eng.cfg.is_primary and not eng.db.val("SELECT COUNT(*) FROM profiles", (), 0):
        with eng.db.tx() as c:
            c.execute("INSERT INTO profiles(name,kind,tier,monitoring,color,policy,sort,created) VALUES(?,?,?,?,?,?,?,?)",
                      ("Household", "adult", "basic", "off", "slate", "{}", 1000, time.time()))
            eng.db.bump_config()


def build():
    logging.basicConfig(level=os.environ.get("BW_LOG", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    eng = Engine(cfg)
    if cfg.demo:
        from . import seed
        seed.run(eng)
    else:
        ensure_defaults(eng)
    web_dir = Path(os.environ.get("BW_WEB_DIR", Path(__file__).resolve().parents[2] / "web" / "dist"))
    return eng, create_app(cfg, eng, web_dir)


def main() -> None:
    eng, app = build()
    eng.run_background()
    uvicorn.run(app, host=os.environ.get("BW_HTTP_HOST", "0.0.0.0"), port=cfg.http_port, log_level="warning")


if __name__ == "__main__":
    main()
