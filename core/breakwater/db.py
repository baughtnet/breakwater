"""SQLite storage.

Two kinds of tables:

* **config** (profiles, devices, settings, users) — authored on the primary and
  copied wholesale to standbys.
* **facts** — an append-only log keyed by (origin node, sequence). Every node
  appends what it observes and pulls what its peers observed. Everything else
  (activity, events, alerts, locks, leases) is derived by applying facts, so
  all nodes converge on the same picture.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'kid',
  color TEXT NOT NULL DEFAULT 'teal', tier TEXT NOT NULL DEFAULT 'strict',
  monitoring TEXT NOT NULL DEFAULT 'full', policy TEXT NOT NULL DEFAULT '{}',
  sort INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS devices(
  id INTEGER PRIMARY KEY, mac TEXT NOT NULL UNIQUE, name TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'other', profile_id INTEGER, route TEXT,
  inspect INTEGER NOT NULL DEFAULT 0, ip TEXT, overrides TEXT NOT NULL DEFAULT '{}',
  hostname TEXT NOT NULL DEFAULT '', created REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, pw_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS facts(
  origin TEXT NOT NULL, seq INTEGER NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY(origin, seq)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS activity(
  mac TEXT NOT NULL, bucket INTEGER NOT NULL, site TEXT NOT NULL, category TEXT NOT NULL,
  queries INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '', seconds REAL NOT NULL DEFAULT 0,
  PRIMARY KEY(mac, bucket, site)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS activity_bucket ON activity(bucket);
CREATE TABLE IF NOT EXISTS usage(
  mac TEXT NOT NULL, minute INTEGER NOT NULL, up INTEGER NOT NULL DEFAULT 0, down INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(mac, minute)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS dirty(mac TEXT NOT NULL, bucket INTEGER NOT NULL, PRIMARY KEY(mac, bucket)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, origin TEXT NOT NULL, seq INTEGER NOT NULL, idx INTEGER NOT NULL,
  ts REAL NOT NULL, mac TEXT NOT NULL, kind TEXT NOT NULL, host TEXT NOT NULL DEFAULT '',
  data TEXT NOT NULL DEFAULT '{}', UNIQUE(origin, seq, idx));
CREATE INDEX IF NOT EXISTS events_mac_ts ON events(mac, kind, ts);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, ts REAL NOT NULL, mac TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL, severity TEXT NOT NULL DEFAULT 'info', title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '', acked INTEGER NOT NULL DEFAULT 0, acked_ts REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS alerts_ts ON alerts(ts);
CREATE TABLE IF NOT EXISTS leases(mac TEXT PRIMARY KEY, ip TEXT NOT NULL, hostname TEXT NOT NULL DEFAULT '',
  ts REAL NOT NULL, expires REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS neigh(ip TEXT PRIMARY KEY, mac TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS locks(target TEXT PRIMARY KEY, locked INTEGER NOT NULL, until REAL NOT NULL DEFAULT 0,
  ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS grants(profile_id INTEGER PRIMARY KEY, until REAL NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS seen(mac TEXT PRIMARY KEY, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS nodes(node_id TEXT PRIMARY KEY, ts REAL NOT NULL, status TEXT NOT NULL);
"""

CONFIG_TABLES = ("profiles", "devices", "settings", "users")


class DB:
    """Thread-safe wrapper: one connection per thread, one writer at a time."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        self._local = threading.local()
        self._write = threading.RLock()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._memory_conn: sqlite3.Connection | None = None
        with self._write:
            self.conn().executescript(SCHEMA)  # executescript manages its own transaction

    def conn(self) -> sqlite3.Connection:
        if self.path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = self._open()
            return self._memory_conn
        c = getattr(self._local, "c", None)
        if c is None:
            c = self._local.c = self._open()
        return c

    def _open(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30, check_same_thread=False, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=OFF")
        return c

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Serialised write transaction (re-entrant)."""
        with self._write:
            c = self.conn()
            nested = c.in_transaction
            if not nested:
                c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                if not nested:
                    c.execute("ROLLBACK")
                raise
            else:
                if not nested:
                    c.execute("COMMIT")

    def q(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        if self.path == ":memory:":
            with self._write:
                return self.conn().execute(sql, tuple(args)).fetchall()
        return self.conn().execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def val(self, sql: str, args: Iterable[Any] = (), default: Any = None) -> Any:
        r = self.one(sql, args)
        return r[0] if r is not None and r[0] is not None else default

    # --- small key/value helpers -------------------------------------------------
    def meta_get(self, key: str, default: Any = None) -> Any:
        v = self.val("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(v) if v is not None else default

    def meta_set(self, key: str, value: Any) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value)))

    def setting(self, key: str, default: Any = None) -> Any:
        v = self.val("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(v) if v is not None else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value)))
            self.bump_config(c)

    # --- config versioning -------------------------------------------------------
    def bump_config(self, c: sqlite3.Connection | None = None) -> None:
        cur = self.meta_get("config_version", 0)
        self.meta_set("config_version", cur + 1)

    def config_version(self) -> int:
        return self.meta_get("config_version", 0)

    def config_snapshot(self) -> dict[str, Any]:
        snap: dict[str, Any] = {"version": self.config_version()}
        for t in CONFIG_TABLES:
            snap[t] = [dict(r) for r in self.q(f"SELECT * FROM {t}")]
        return snap

    def load_config_snapshot(self, snap: dict[str, Any]) -> None:
        with self.tx() as c:
            for t in CONFIG_TABLES:
                c.execute(f"DELETE FROM {t}")
                for row in snap.get(t, []):
                    cols = ",".join(row.keys())
                    marks = ",".join("?" for _ in row)
                    c.execute(f"INSERT INTO {t}({cols}) VALUES({marks})", tuple(row.values()))
            self.meta_set("config_version", snap["version"])


def row_dict(r: sqlite3.Row | None, json_cols: tuple[str, ...] = ()) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for col in json_cols:
        if col in d and isinstance(d[col], str):
            d[col] = json.loads(d[col] or "{}")
    return d
