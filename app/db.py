"""SQLite persistence: sessions + messages."""
from __future__ import annotations
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional

from .settings import DB_FILE

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_FILE), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


_conn: Optional[sqlite3.Connection] = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _connect()
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            mode TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            meta_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            speaker TEXT,
            provider_id TEXT,
            model_id TEXT,
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            meta_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

        -- Full-text search: trigram FTS5 mirroring messages.content.
        -- Trigram tokenizer handles CJK substring search out of the box,
        -- no separate jieba native dep required.
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content,
            tokenize='trigram'
        );

        -- Keep FTS in lock-step with messages via triggers so raw DELETEs
        -- (e.g. the regenerate flow) can't leave the index out of sync.
        CREATE TRIGGER IF NOT EXISTS messages_ai
        AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS messages_ad
        AFTER DELETE ON messages BEGIN
            DELETE FROM messages_fts WHERE rowid = old.rowid;
        END;
        CREATE TRIGGER IF NOT EXISTS messages_au
        AFTER UPDATE OF content ON messages BEGIN
            DELETE FROM messages_fts WHERE rowid = old.rowid;
            INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END;
        """
    )
    # Backfill the FTS index for any rows that exist before the table was created.
    have_fts_rows = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    have_msg_rows = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    if have_msg_rows and not have_fts_rows:
        conn.execute(
            "INSERT INTO messages_fts (rowid, content) SELECT rowid, content FROM messages"
        )


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    with _lock:
        try:
            conn.execute("BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


# ----- Sessions -----
def create_session(title: str, mode: str, meta: dict | None = None) -> dict:
    sid = new_id()
    ts = now()
    with tx() as c:
        c.execute(
            "INSERT INTO sessions (id, title, mode, created_at, updated_at, meta_json) VALUES (?,?,?,?,?,?)",
            (sid, title, mode, ts, ts, json.dumps(meta or {})),
        )
    return get_session(sid)


def get_session(sid: str) -> dict | None:
    row = get_conn().execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    return _session_row(row) if row else None


def list_sessions(query: str | None = None) -> list[dict]:
    if query:
        rows = (
            get_conn()
            .execute(
                "SELECT * FROM sessions WHERE title LIKE ? ORDER BY updated_at DESC",
                (f"%{query}%",),
            )
            .fetchall()
        )
    else:
        rows = (
            get_conn()
            .execute("SELECT * FROM sessions ORDER BY updated_at DESC")
            .fetchall()
        )
    return [_session_row(r) for r in rows]


def update_session(sid: str, *, title: str | None = None, mode: str | None = None, meta: dict | None = None) -> dict | None:
    fields, params = [], []
    if title is not None:
        fields.append("title=?")
        params.append(title)
    if mode is not None:
        fields.append("mode=?")
        params.append(mode)
    if meta is not None:
        fields.append("meta_json=?")
        params.append(json.dumps(meta))
    fields.append("updated_at=?")
    params.append(now())
    params.append(sid)
    with tx() as c:
        c.execute(f"UPDATE sessions SET {', '.join(fields)} WHERE id=?", params)
    return get_session(sid)


def touch_session(sid: str) -> None:
    with tx() as c:
        c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now(), sid))


def delete_session(sid: str) -> bool:
    with tx() as c:
        # Delete messages explicitly so the FTS triggers fire — ON DELETE CASCADE
        # bypasses row triggers, which would leave the FTS index stale.
        c.execute("DELETE FROM messages WHERE session_id=?", (sid,))
        cur = c.execute("DELETE FROM sessions WHERE id=?", (sid,))
        return cur.rowcount > 0


def search_messages(query: str, *, limit: int = 30) -> list[dict]:
    """Trigram FTS5 search across every message in every session.

    Returns a flat list ordered by rank (best match first), each entry
    enriched with the parent session's title/mode and a snippet with
    the matched span wrapped in <mark>...</mark> tags.
    """
    q = (query or "").strip()
    if not q:
        return []
    # FTS5 MATCH treats the query as a syntax string; wrap in quotes so
    # punctuation and CJK chars are taken literally.
    fts_q = '"' + q.replace('"', '""') + '"'
    rows = get_conn().execute(
        """
        SELECT
            m.id AS message_id,
            m.session_id AS session_id,
            m.role AS role,
            m.speaker AS speaker,
            m.created_at AS created_at,
            s.title AS session_title,
            s.mode AS session_mode,
            snippet(messages_fts, 0, '<mark>', '</mark>', '…', 12) AS snippet
        FROM messages_fts
        JOIN messages m ON m.rowid = messages_fts.rowid
        JOIN sessions s ON s.id = m.session_id
        WHERE messages_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (fts_q, max(1, min(int(limit), 200))),
    ).fetchall()
    return [dict(r) for r in rows]


def _session_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "mode": row["mode"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "meta": json.loads(row["meta_json"] or "{}"),
    }


# ----- Messages -----
def add_message(
    session_id: str,
    role: str,
    content: str,
    *,
    speaker: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    meta: dict | None = None,
) -> dict:
    mid = new_id()
    ts = now()
    with tx() as c:
        c.execute(
            "INSERT INTO messages (id, session_id, role, speaker, provider_id, model_id, content, created_at, meta_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                mid,
                session_id,
                role,
                speaker,
                provider_id,
                model_id,
                content,
                ts,
                json.dumps(meta or {}),
            ),
        )
        c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (ts, session_id))
    return get_message(mid)


def update_message(mid: str, *, content: str | None = None, meta: dict | None = None) -> dict | None:
    fields, params = [], []
    if content is not None:
        fields.append("content=?")
        params.append(content)
    if meta is not None:
        fields.append("meta_json=?")
        params.append(json.dumps(meta))
    if not fields:
        return get_message(mid)
    params.append(mid)
    with tx() as c:
        c.execute(f"UPDATE messages SET {', '.join(fields)} WHERE id=?", params)
    return get_message(mid)


def get_message(mid: str) -> dict | None:
    row = get_conn().execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    return _message_row(row) if row else None


def list_messages(session_id: str) -> list[dict]:
    rows = (
        get_conn()
        .execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY created_at ASC",
            (session_id,),
        )
        .fetchall()
    )
    return [_message_row(r) for r in rows]


def _message_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "speaker": row["speaker"],
        "provider_id": row["provider_id"],
        "model_id": row["model_id"],
        "content": row["content"],
        "created_at": row["created_at"],
        "meta": json.loads(row["meta_json"] or "{}"),
    }
