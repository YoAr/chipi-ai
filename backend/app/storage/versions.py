"""Versioned documents (spec v2 §4: Profile and Service are versioned, with an approval state).

Every save is a new version; nothing is overwritten. Approving marks one version approved
(and any earlier approved version superseded). Plain functions over the connection.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .db import WRITE_LOCK

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _out(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "name": row["name"], "version": row["version"], "status": row["status"], "author": row["author"],
        "data": json.loads(row["json"]),
        "provenance": json.loads(row["provenance"]) if row["provenance"] else None,
        "checks": json.loads(row["checks"]) if row["checks"] else [],
        "created_at": row["created_at"], "approved_at": row["approved_at"],
    }


def save(conn: sqlite3.Connection, name: str, data: dict, *, author: str,
         provenance: dict | None = None, checks: list[dict] | None = None) -> dict:
    with WRITE_LOCK, conn:
        n = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM doc_versions WHERE name = ?", (name,)).fetchone()[0]
        conn.execute(
            "INSERT INTO doc_versions (name, version, json, status, author, provenance, checks, created_at) "
            "VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)",
            (name, n, json.dumps(data, ensure_ascii=False), author,
             json.dumps(provenance) if provenance else None, json.dumps(checks or []), _now()),
        )
    return get(conn, name, n)


def get(conn: sqlite3.Connection, name: str, version: int) -> dict | None:
    return _out(conn.execute("SELECT * FROM doc_versions WHERE name = ? AND version = ?", (name, version)).fetchone())


def latest(conn: sqlite3.Connection, name: str) -> dict | None:
    return _out(conn.execute("SELECT * FROM doc_versions WHERE name = ? ORDER BY version DESC LIMIT 1",
                             (name,)).fetchone())


def approved(conn: sqlite3.Connection, name: str) -> dict | None:
    return _out(conn.execute("SELECT * FROM doc_versions WHERE name = ? AND status = 'approved' "
                             "ORDER BY version DESC LIMIT 1", (name,)).fetchone())


def approve(conn: sqlite3.Connection, name: str, version: int) -> dict:
    with WRITE_LOCK, conn:
        row = conn.execute("SELECT status FROM doc_versions WHERE name = ? AND version = ?", (name, version)).fetchone()
        if row is None:
            from ..mock_store import NotFound
            raise NotFound(f"No {name} version {version}")
        if row["status"] != "draft":
            raise ValueError(f"{name} version {version} is {row['status']}, not a draft")
        conn.execute("UPDATE doc_versions SET status = 'superseded' WHERE name = ? AND status = 'approved'", (name,))
        conn.execute("UPDATE doc_versions SET status = 'approved', approved_at = ? WHERE name = ? AND version = ?",
                     (_now(), name, version))
    return get(conn, name, version)
