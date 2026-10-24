"""Persistencia en SQLite (modo WAL).

SQLite es suficiente para una instalación de un solo nodo. El acceso está
encapsulado en este módulo para poder migrar a PostgreSQL sin tocar el resto.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    pw_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    aspect TEXT NOT NULL,
    settings TEXT NOT NULL DEFAULT '{}',
    use_declaration TEXT,
    brief TEXT NOT NULL DEFAULT '',
    storyboard TEXT NOT NULL DEFAULT '{}',
    timeline TEXT NOT NULL DEFAULT '{}',
    timeline_version INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    origin TEXT NOT NULL DEFAULT '{}',
    provenance TEXT,
    fragment TEXT,
    original_asset_id TEXT,
    proxy_asset_id TEXT,
    working_asset_id TEXT,
    review_asset_id TEXT,
    sprite_asset_id TEXT,
    video_history TEXT NOT NULL DEFAULT '[]',
    analysis TEXT,
    duration REAL,
    width INTEGER,
    height INTEGER,
    fps REAL,
    error TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    identity_id TEXT REFERENCES identities(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    mime TEXT,
    size INTEGER,
    sha256 TEXT,
    duration REAL,
    width INTEGER,
    height INTEGER,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    expires_at REAL
);
CREATE TABLE IF NOT EXISTS cast_members (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    identity_id TEXT REFERENCES identities(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    style TEXT NOT NULL,
    voice_id TEXT,
    portrait_asset_id TEXT,
    portrait_preview_asset_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    meta TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS identities (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    is_minor INTEGER NOT NULL DEFAULT 0,
    consent_status TEXT NOT NULL DEFAULT 'pending',
    scopes TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    revoked_at REAL
);
CREATE TABLE IF NOT EXISTS consents (
    id TEXT PRIMARY KEY,
    identity_id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
    token_hash TEXT UNIQUE,
    status TEXT NOT NULL,
    method TEXT NOT NULL,
    scopes TEXT NOT NULL,
    purpose TEXT NOT NULL,
    statement_version TEXT NOT NULL,
    statement_hash TEXT NOT NULL,
    signer_name TEXT,
    signer_role TEXT,
    created_at REAL NOT NULL,
    granted_at REAL,
    revoked_at REAL,
    expires_at REAL,
    ip_hash TEXT,
    ua_hash TEXT
);
CREATE TABLE IF NOT EXISTS dialogues (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    mode TEXT NOT NULL,
    text TEXT,
    settings TEXT NOT NULL DEFAULT '{}',
    audio_asset_id TEXT,
    duration REAL,
    estimated_duration REAL,
    segments TEXT NOT NULL DEFAULT '[]',
    subtitles TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    project_id TEXT,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    message TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    result TEXT,
    error TEXT,
    error_code TEXT,
    checkpoint TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    locked_by TEXT,
    heartbeat_at REAL,
    next_run_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, next_run_at);
CREATE INDEX IF NOT EXISTS sources_project ON sources(project_id);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    project_id TEXT,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    units REAL NOT NULL,
    unit TEXT NOT NULL,
    cost REAL NOT NULL,
    job_id TEXT,
    created_at REAL NOT NULL
);
"""

SCHEMA_VERSION = 3

_local = threading.local()
_init_lock = threading.Lock()
_initialized_for: str | None = None


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


def _connect() -> sqlite3.Connection:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.database_path), timeout=30, isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def conn() -> sqlite3.Connection:
    global _initialized_for
    path = str(settings.database_path)
    c: sqlite3.Connection | None = getattr(_local, "conn", None)
    if c is None or getattr(_local, "path", None) != path:
        c = _connect()
        _local.conn = c
        _local.path = path
    with _init_lock:
        if _initialized_for != path:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            has_tables = c.execute("SELECT count(*) FROM sqlite_master WHERE name='projects'").fetchone()[0]
            if has_tables and version < SCHEMA_VERSION:
                raise RuntimeError("La base de datos es de una versión anterior sin migración automática. "
                                   "Haga una copia y elimine DATA_DIR para crearla de nuevo.")
            c.executescript(SCHEMA)
            c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            _initialized_for = path
    return c


def reset_connections() -> None:
    global _initialized_for
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
    _local.conn = None
    _initialized_for = None


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


def execute(sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
    return conn().execute(sql, params)


def one(sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
    row = conn().execute(sql, params).fetchone()
    return dict(row) if row else None


def all_(sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def jdump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def jload(v: str | None, default: Any = None) -> Any:
    if v in (None, ""):
        return default
    return json.loads(v)


def audit(action: str, user_id: str | None = None, project_id: str | None = None, **detail: Any) -> None:
    execute(
        "INSERT INTO audit(user_id, project_id, action, detail, created_at) VALUES (?,?,?,?,?)",
        (user_id, project_id, action, jdump(detail), now()),
    )
