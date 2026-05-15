"""
All SQLite operations for nanoclaw-amplifier.
Matches nanoclaw's exact schema from src/db/schema.ts.
"""
from __future__ import annotations
import json, logging, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

OUTBOUND_INIT_SQL = """
CREATE TABLE IF NOT EXISTS messages_out (
    id            TEXT PRIMARY KEY,
    seq           INTEGER UNIQUE,
    in_reply_to   TEXT,
    timestamp     TEXT NOT NULL DEFAULT (datetime('now')),
    deliver_after TEXT,
    recurrence    TEXT,
    kind          TEXT NOT NULL,
    platform_id   TEXT,
    channel_type  TEXT,
    thread_id     TEXT,
    content       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processing_ack (
    message_id     TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    status_changed TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS container_state (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),
    current_tool             TEXT,
    tool_declared_timeout_ms INTEGER,
    tool_started_at          TEXT,
    updated_at               TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS delivered (
    message_out_id      TEXT PRIMARY KEY,
    platform_message_id TEXT,
    status              TEXT NOT NULL DEFAULT 'delivered',
    delivered_at        TEXT NOT NULL
);
"""

INBOUND_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS messages_in (
    id              TEXT PRIMARY KEY,
    seq             INTEGER UNIQUE,
    kind            TEXT NOT NULL,
    timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
    status          TEXT DEFAULT 'pending',
    process_after   TEXT,
    recurrence      TEXT,
    series_id       TEXT,
    tries           INTEGER DEFAULT 0,
    trigger         INTEGER NOT NULL DEFAULT 1,
    platform_id     TEXT,
    channel_type    TEXT,
    thread_id       TEXT,
    content         TEXT NOT NULL,
    source_session_id TEXT,
    on_wake         INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS session_routing (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    channel_type TEXT,
    platform_id  TEXT,
    thread_id    TEXT
);
CREATE TABLE IF NOT EXISTS destinations (
    name         TEXT PRIMARY KEY,
    display_name TEXT,
    type         TEXT NOT NULL,
    channel_type TEXT,
    platform_id  TEXT,
    agent_group_id TEXT
);
"""

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def init_outbound(conn: sqlite3.Connection) -> None:
    conn.executescript(OUTBOUND_INIT_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO container_state "
        "(id, current_tool, tool_declared_timeout_ms, tool_started_at, updated_at) "
        "VALUES (1, NULL, NULL, NULL, ?)", (_now_iso(),)
    )
    conn.commit()

def init_inbound(conn: sqlite3.Connection) -> None:
    """Create inbound tables (for testing only - production host owns these)."""
    conn.executescript(INBOUND_SCHEMA_SQL)
    conn.commit()

def clear_stale_processing(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM processing_ack WHERE status='processing'"
    )
    conn.commit()

# ── Inbound reads ─────────────────────────────────────────────────────────────

def fetch_pending(conn: sqlite3.Connection, limit: int = 10) -> list:
    try:
        return conn.execute(
            "SELECT id, seq, kind, content, platform_id, channel_type, thread_id "
            "FROM messages_in "
            "WHERE status='pending' AND trigger=1 "
            "AND (process_after IS NULL OR process_after <= datetime('now')) "
            "ORDER BY seq ASC LIMIT ?",
            (limit,)
        ).fetchall()
    except sqlite3.OperationalError:
        return []

def mark_inbound_status(conn: sqlite3.Connection, ids: list[str], status: str) -> None:
    """Update messages_in.status — tells host we're processing/done with these messages."""
    try:
        now = _now_iso()
        conn.executemany(
            "UPDATE messages_in SET status=?, tries=tries+1 WHERE id=?",
            [(status, mid) for mid in ids]
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Table might not exist yet — non-fatal

def fetch_routing(conn: sqlite3.Connection) -> dict:
    try:
        row = conn.execute("SELECT channel_type, platform_id, thread_id FROM session_routing LIMIT 1").fetchone()
        if row:
            return {"channel_type": row["channel_type"] or "",
                    "platform_id": row["platform_id"] or "",
                    "thread_id": row["thread_id"]}
    except sqlite3.OperationalError:
        pass
    return {"channel_type": "", "platform_id": "", "thread_id": None}

def fetch_destinations(conn: sqlite3.Connection) -> dict[str, dict]:
    try:
        rows = conn.execute("SELECT name, channel_type, platform_id FROM destinations").fetchall()
        return {r["name"]: dict(r) for r in rows}
    except Exception:
        return {}

def set_routing(conn: sqlite3.Connection, channel_type: str, platform_id: str, thread_id: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO session_routing (id, channel_type, platform_id, thread_id) "
        "VALUES (1, ?, ?, ?)", (channel_type, platform_id, thread_id)
    )
    conn.commit()

# ── Outbound writes ───────────────────────────────────────────────────────────

def next_odd_seq(in_conn: sqlite3.Connection, out_conn: sqlite3.Connection) -> int:
    r1 = in_conn.execute("SELECT COALESCE(MAX(seq),0) FROM messages_in").fetchone()[0]
    r2 = out_conn.execute("SELECT COALESCE(MAX(seq),0) FROM messages_out").fetchone()[0]
    base = max(r1, r2)
    n = base + 1
    return n if n % 2 == 1 else n + 1

def ack_batch(conn: sqlite3.Connection, message_ids: list[str], status: str) -> None:
    now = _now_iso()
    conn.executemany(
        "INSERT OR REPLACE INTO processing_ack (message_id, status, status_changed) VALUES (?, ?, ?)",
        [(mid, status, now) for mid in message_ids]
    )
    conn.commit()

def update_container_state(conn: sqlite3.Connection, tool_name: str | None, timeout_ms: int | None = None) -> None:
    now = _now_iso()
    conn.execute(
        "UPDATE container_state SET current_tool=?, tool_declared_timeout_ms=?, "
        "tool_started_at=?, updated_at=? WHERE id=1",
        (tool_name, timeout_ms, now if tool_name else None, now)
    )
    conn.commit()

def save_context(conn: sqlite3.Connection, messages: list) -> None:
    """Persist conversation context to session_state for cross-restart resume."""
    try:
        serialized = []
        for m in messages:
            if hasattr(m, 'model_dump'):
                serialized.append(m.model_dump())
            elif isinstance(m, dict):
                serialized.append(m)
            else:
                serialized.append({"role": "system", "content": str(m)})
        value = json.dumps(serialized)
        conn.execute(
            "INSERT OR REPLACE INTO session_state (key, value, updated_at) VALUES ('context', ?, ?)",
            (value, _now_iso())
        )
        conn.commit()
    except Exception as e:
        log.warning(f"Failed to save context: {e}")

def load_context(conn: sqlite3.Connection) -> list[dict]:
    """Load persisted context. Returns [] if none."""
    try:
        row = conn.execute("SELECT value FROM session_state WHERE key='context'").fetchone()
        if row:
            return json.loads(row[0])
    except Exception as e:
        log.warning(f"Failed to load context: {e}")
    return []
