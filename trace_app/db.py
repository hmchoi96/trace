"""SQLite state for the Trace app. Postgres-compatible column shapes."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Iterable

DEFAULT_DB_PATH = os.environ.get("TRACE_DB_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "runs", "trace_app.db"
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS profiles (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    product_name      TEXT NOT NULL,
    hunt_description  TEXT NOT NULL DEFAULT '',
    sender_name       TEXT NOT NULL DEFAULT '',
    sender_company    TEXT NOT NULL DEFAULT '',
    from_email        TEXT NOT NULL DEFAULT '',
    default_template  TEXT NOT NULL,
    profile_json      TEXT NOT NULL,
    builtin           INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hunts (
    id             TEXT PRIMARY KEY,
    profile_id     TEXT NOT NULL REFERENCES profiles(id),
    snapshot_json  TEXT NOT NULL,
    limit_n        INTEGER NOT NULL,
    status         TEXT NOT NULL,
    error          TEXT,
    current_stage  TEXT NOT NULL DEFAULT '',
    started_at     TEXT,
    estimate_sec   INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    finished_at    TEXT
);

CREATE TABLE IF NOT EXISTS hunt_events (
    id          TEXT PRIMARY KEY,
    hunt_id     TEXT NOT NULL REFERENCES hunts(id),
    stage       TEXT NOT NULL,
    message     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hunt_events_hunt ON hunt_events(hunt_id);

CREATE TABLE IF NOT EXISTS candidates (
    id              TEXT PRIMARY KEY,
    hunt_id         TEXT REFERENCES hunts(id),
    profile_id      TEXT NOT NULL REFERENCES profiles(id),
    name            TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    company         TEXT NOT NULL DEFAULT '',
    found_on        TEXT NOT NULL DEFAULT '',
    entity_key      TEXT NOT NULL DEFAULT '',
    decision        TEXT NOT NULL DEFAULT 'pending',
    outcome         TEXT,
    email           TEXT NOT NULL DEFAULT '',
    email_source    TEXT NOT NULL DEFAULT '',
    phone           TEXT NOT NULL DEFAULT '',
    phone_source    TEXT NOT NULL DEFAULT '',
    enrich_state    TEXT NOT NULL DEFAULT 'none',
    candidate_json  TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    decided_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_profile ON candidates(profile_id);
CREATE INDEX IF NOT EXISTS idx_candidates_entity ON candidates(profile_id, entity_key);

CREATE TABLE IF NOT EXISTS drafts (
    id             TEXT PRIMARY KEY,
    candidate_id   TEXT NOT NULL REFERENCES candidates(id),
    profile_id     TEXT NOT NULL REFERENCES profiles(id),
    template_id    TEXT NOT NULL,
    subject        TEXT NOT NULL DEFAULT '',
    body           TEXT NOT NULL DEFAULT '',
    verdict        TEXT NOT NULL,
    sendable       INTEGER NOT NULL DEFAULT 0,
    critique_json  TEXT,
    error          TEXT,
    superseded     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drafts_candidate ON drafts(candidate_id);

CREATE TABLE IF NOT EXISTS sends (
    id                   TEXT PRIMARY KEY,
    draft_id             TEXT REFERENCES drafts(id),
    candidate_id         TEXT NOT NULL REFERENCES candidates(id),
    profile_id           TEXT NOT NULL REFERENCES profiles(id),
    method               TEXT NOT NULL,
    to_email             TEXT NOT NULL DEFAULT '',
    from_email           TEXT NOT NULL DEFAULT '',
    subject              TEXT NOT NULL DEFAULT '',
    body                 TEXT NOT NULL DEFAULT '',
    graph_message_id     TEXT,
    conversation_id      TEXT,
    internet_message_id  TEXT,
    idempotency_key      TEXT NOT NULL UNIQUE,
    sent_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sends_candidate ON sends(candidate_id);

CREATE TABLE IF NOT EXISTS notes (
    id            TEXT PRIMARY KEY,
    candidate_id  TEXT NOT NULL REFERENCES candidates(id),
    text          TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cost_events (
    id          TEXT PRIMARY KEY,
    profile_id  TEXT NOT NULL,
    hunt_id     TEXT,
    stage       TEXT NOT NULL DEFAULT '',
    cost_usd    REAL NOT NULL DEFAULT 0,
    elapsed_sec REAL NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cost_profile ON cost_events(profile_id);

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    profile_id   TEXT,
    hunt_id      TEXT,
    candidate_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL,
    progress     TEXT NOT NULL DEFAULT '',
    error        TEXT,
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""

_local = threading.local()


def connect(path: str | None = None) -> sqlite3.Connection:
    """One connection per thread. SQLite objects are not thread safe."""
    target = os.path.abspath(path or os.environ.get("TRACE_DB_PATH") or DEFAULT_DB_PATH)
    existing = getattr(_local, "conn", None)
    if existing is not None and getattr(_local, "path", None) == target:
        return existing
    if existing is not None:
        existing.close()
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    conn = sqlite3.connect(target, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    _local.conn = conn
    _local.path = target
    return conn


def reset_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
    _local.conn = None
    _local.path = None


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def loads(value: Any, fallback: Any = None) -> Any:
    if not value:
        return {} if fallback is None else fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {} if fallback is None else fallback


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after first deploy without recreating the DB."""
    hunt_cols = {row[1] for row in conn.execute("PRAGMA table_info(hunts)").fetchall()}
    if "current_stage" not in hunt_cols:
        conn.execute("ALTER TABLE hunts ADD COLUMN current_stage TEXT NOT NULL DEFAULT ''")
    if "started_at" not in hunt_cols:
        conn.execute("ALTER TABLE hunts ADD COLUMN started_at TEXT")
    if "estimate_sec" not in hunt_cols:
        conn.execute("ALTER TABLE hunts ADD COLUMN estimate_sec INTEGER NOT NULL DEFAULT 0")
    conn.commit()
