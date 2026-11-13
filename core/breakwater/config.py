"""Runtime configuration, read once from the environment."""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Config:
    node_id: str = field(default_factory=lambda: _env("BW_NODE_ID") or socket.gethostname())
    role: str = field(default_factory=lambda: _env("BW_ROLE", "primary"))  # primary | standby
    peers: list[str] = field(
        default_factory=lambda: [p.strip().rstrip("/") for p in _env("BW_PEERS").split(",") if p.strip()]
    )
    primary_url: str = field(default_factory=lambda: _env("BW_PRIMARY_URL").rstrip("/"))
    token: str = field(default_factory=lambda: _env("BW_CLUSTER_TOKEN"))
    data_dir: Path = field(default_factory=lambda: Path(_env("BW_DATA", "/data")))
    http_port: int = field(default_factory=lambda: int(_env("BW_HTTP_PORT", "8080")))
    tz_name: str = field(default_factory=lambda: _env("BW_TZ", "America/Vancouver"))

    adguard_strict_url: str = field(default_factory=lambda: _env("BW_ADGUARD_STRICT_URL", "http://127.0.0.1:3001"))
    adguard_basic_url: str = field(default_factory=lambda: _env("BW_ADGUARD_BASIC_URL", "http://127.0.0.1:3002"))
    adguard_user: str = field(default_factory=lambda: _env("BW_ADGUARD_USER", "breakwater"))
    adguard_password: str = field(default_factory=lambda: _env("BW_ADGUARD_PASSWORD"))
    adguard_enabled: bool = field(default_factory=lambda: _env("BW_ADGUARD", "1") == "1")

    # LAN facts the gateway needs; core only passes them through.
    lan_cidr: str = field(default_factory=lambda: _env("BW_LAN_CIDR", "192.168.0.0/24"))
    router_ip: str = field(default_factory=lambda: _env("BW_ROUTER_IP", "192.168.0.1"))
    vip: str = field(default_factory=lambda: _env("BW_VIP"))
    node_ip: str = field(default_factory=lambda: _env("BW_NODE_IP"))

    demo: bool = field(default_factory=lambda: _env("BW_DEMO") == "1")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    @property
    def is_primary(self) -> bool:
        return self.role == "primary"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "breakwater.db"


cfg = Config()
