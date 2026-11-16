"""Work queue for model jobs the laptop routine picks up (table `queue`).

Statuses: pending -> taken -> done | failed. Priorities: urgent, normal.
"""

import json
import sqlite3
from datetime import datetime, timezone

from ..mock_store import NotFound
from .db import WRITE_LOCK

PRIORITIES = ("urgent", "normal")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    raw = item.pop("payload_json", None)
    item["payload"] = json.loads(raw) if raw else None
    return item


def enqueue_unlocked(
    conn: sqlite3.Connection, kind: str, ref: str | None, priority: str = "normal", payload: dict | None = None
) -> int:
    """Insert a pending item inside the caller's transaction. Caller holds WRITE_LOCK and commits."""
    if not kind or not isinstance(kind, str):
        raise ValueError("Queue kind is required")
    if priority not in PRIORITIES:
        raise ValueError(f"Unknown priority {priority!r}; use urgent or normal")
    cur = conn.execute(
        "INSERT INTO queue (kind, ref, priority, status, payload_json, created_at) VALUES (?, ?, ?, 'pending', ?, ?)",
        (kind, ref, priority, json.dumps(payload) if payload is not None else None, _now()),
    )
    return int(cur.lastrowid)


def enqueue(
    conn: sqlite3.Connection, kind: str, ref: str | None, priority: str = "normal", payload: dict | None = None
) -> int:
    with WRITE_LOCK, conn:
        return enqueue_unlocked(conn, kind, ref, priority, payload)


def take_next(conn: sqlite3.Connection) -> dict | None:
    """Claim the next pending item (urgent first, then oldest) in one UPDATE, so no two callers get the same row."""
    with WRITE_LOCK, conn:
        row = conn.execute(
            """
            UPDATE queue SET status = 'taken', taken_at = ?
            WHERE status = 'pending' AND id = (
                SELECT id FROM queue WHERE status = 'pending'
                ORDER BY CASE priority WHEN 'urgent' THEN 0 ELSE 1 END, id
                LIMIT 1
            )
            RETURNING *
            """,
            (_now(),),
        ).fetchall()  # fetch fully so the statement finishes before commit
    return _row(row[0]) if row else None


def complete(conn: sqlite3.Connection, id: int, ok: bool, error: str | None = None) -> dict:
    """Mark an item done (ok) or failed (with error). Raises NotFound / ValueError if already finished."""
    with WRITE_LOCK, conn:
        row = conn.execute("SELECT status FROM queue WHERE id = ?", (id,)).fetchone()
        if row is None:
            raise NotFound(f"No queue item with id {id}")
        if row["status"] in ("done", "failed"):
            raise ValueError(f"Queue item {id} is already {row['status']}")
        updated = conn.execute(
            "UPDATE queue SET status = ?, done_at = ?, error = ? WHERE id = ? RETURNING *",
            ("done" if ok else "failed", _now(), None if ok else (error or "failed"), id),
        ).fetchall()
    return _row(updated[0])


def finish_for_ref_unlocked(conn: sqlite3.Connection, kind: str, ref: str) -> int | None:
    """Mark the open (pending or taken) items of this kind for `ref` done, inside the caller's
    transaction (caller holds WRITE_LOCK and commits). Returns the first such item's id, else
    the id of an item already done for it, else None."""
    rows = conn.execute(
        "SELECT id, status FROM queue WHERE kind = ? AND ref = ? ORDER BY id", (kind, ref)
    ).fetchall()
    open_ids = [r["id"] for r in rows if r["status"] in ("pending", "taken")]
    if open_ids:
        marks = ",".join("?" * len(open_ids))
        conn.execute(
            f"UPDATE queue SET status = 'done', done_at = ?, error = NULL WHERE id IN ({marks})",
            (_now(), *open_ids),
        )
        return int(open_ids[0])
    done = [r["id"] for r in rows if r["status"] == "done"]
    return int(done[0]) if done else None


def pending(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM queue WHERE status = 'pending' "
        "ORDER BY CASE priority WHEN 'urgent' THEN 0 ELSE 1 END, id"
    ).fetchall()
    return [_row(r) for r in rows]
