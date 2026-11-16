"""SQLite connection and schema.

One file per data dir (`app.db`), WAL mode so reads don't block the writer.
Every write must hold `WRITE_LOCK` (one writer at a time; the lock is not
re-entrant, so never call a locking helper while already holding it).
"""

import sqlite3
import threading
from pathlib import Path

DB_FILENAME = "app.db"

WRITE_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT                       -- JSON
);

CREATE TABLE IF NOT EXISTS docs (
    name       TEXT PRIMARY KEY,     -- persona | fit | offers | metrics
    json       TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS leads (
    id           TEXT PRIMARY KEY,
    nickname     TEXT UNIQUE,
    title        TEXT,
    company      TEXT,
    source       TEXT,
    url          TEXT,
    fetched      INTEGER NOT NULL DEFAULT 0,
    rate_text    TEXT,
    score        INTEGER NULL,
    why          TEXT,
    status       TEXT NOT NULL DEFAULT 'new',
    found_at     TEXT,
    dedup_key    TEXT UNIQUE,
    verdict_note TEXT,
    description  TEXT
);
CREATE INDEX IF NOT EXISTS leads_status ON leads(status);

CREATE TABLE IF NOT EXISTS drafts (
    id               TEXT PRIMARY KEY,
    lead_id          TEXT,
    nickname         TEXT,
    channel          TEXT,
    to_addr          TEXT,
    subject          TEXT,
    body             TEXT,
    template_version TEXT,
    verifier_json    TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       TEXT,
    approved_at      TEXT,
    sent_at          TEXT,
    reject_note      TEXT
);
CREATE INDEX IF NOT EXISTS drafts_status ON drafts(status);

CREATE TABLE IF NOT EXISTS replies (
    id          TEXT PRIMARY KEY,
    lead_id     TEXT,
    received_at TEXT,
    body        TEXT,
    label       TEXT,
    confidence  REAL
);

CREATE TABLE IF NOT EXISTS deadlines (
    id       TEXT PRIMARY KEY,
    lead_id  TEXT,
    nickname TEXT,
    what     TEXT,
    due_at   TEXT,
    done     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    ref          TEXT,
    priority     TEXT NOT NULL DEFAULT 'normal',
    status       TEXT NOT NULL DEFAULT 'pending',
    payload_json TEXT,
    created_at   TEXT,
    taken_at     TEXT,
    done_at      TEXT,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS queue_status ON queue(status, priority, id);

CREATE TABLE IF NOT EXISTS metric_snapshots (
    week    TEXT,
    name    TEXT,
    segment TEXT,
    value   REAL,
    low     REAL,
    high    REAL,
    n       INTEGER,
    PRIMARY KEY (week, name, segment)
);
"""


def connect(data_dir: Path) -> sqlite3.Connection:
    """Open (creating if needed) data_dir/app.db in WAL mode."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(data_dir / DB_FILENAME), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create every table if missing. Safe to run on every start."""
    with WRITE_LOCK:
        conn.executescript(SCHEMA)
        # Databases made before the post text was kept (the live volume) get the column added
        cols = {r[1] for r in conn.execute("PRAGMA table_info(leads)")}
        if "description" not in cols:
            conn.execute("ALTER TABLE leads ADD COLUMN description TEXT")
        conn.commit()
