"""Companies with job boards, and daily posting snapshots of the baseline panel.

Plain functions over the SQLite connection (like queue.py). Company-level data only:
no people (spec v2 §2.6).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .db import WRITE_LOCK


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert_company(conn: sqlite3.Connection, source: str, slug: str, discovered_via: str | None = None,
                   in_panel: bool = False) -> None:
    """Add a company if new; mark it in the panel if asked (never takes a company out)."""
    with WRITE_LOCK, conn:
        conn.execute(
            "INSERT INTO companies (source, slug, discovered_via, in_panel, first_seen) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(source, slug) DO UPDATE SET in_panel = MAX(in_panel, excluded.in_panel)",
            (source, slug, discovered_via, 1 if in_panel else 0, _now()),
        )


def record_fetch(conn: sqlite3.Connection, source: str, slug: str, *, name: str | None = None,
                 domain: str | None = None, sector: str | None = None, error: str | None = None) -> None:
    with WRITE_LOCK, conn:
        conn.execute(
            "UPDATE companies SET last_fetched = ?, last_error = ?, name = COALESCE(?, name), "
            "domain = COALESCE(?, domain), sector = COALESCE(?, sector) WHERE source = ? AND slug = ?",
            (_now(), error, name, domain, sector, source, slug),
        )


def companies(conn: sqlite3.Connection, source: str, panel_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM companies WHERE source = ?" + (" AND in_panel = 1" if panel_only else "")
    return [dict(r) for r in conn.execute(sql + " ORDER BY slug", (source,)).fetchall()]


def save_snapshot(conn: sqlite3.Connection, day: str, source: str, slug: str, postings: list[dict]) -> int:
    """Record today's postings for one panel company (idempotent per day). Returns rows written."""
    rows = [(day, source, slug, p["source_posting_id"], p.get("title"), p.get("employment_type"),
             p.get("published_at"), p.get("language"), json.dumps(p.get("skills") or []))
            for p in postings if p.get("source_posting_id")]
    with WRITE_LOCK, conn:
        conn.executemany(
            "INSERT OR REPLACE INTO posting_snapshots (day, source, company_slug, posting_id, title, "
            "employment_type, published_at, language, skills_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def snapshot_days(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT day FROM posting_snapshots ORDER BY day")]
