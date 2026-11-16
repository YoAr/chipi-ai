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
    verdict_note TEXT
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

-- Spec v2: companies with a public job board (Recruitee for now)
CREATE TABLE IF NOT EXISTS companies (
    source         TEXT NOT NULL,     -- recruitee
    slug           TEXT NOT NULL,     -- {slug}.recruitee.com
    name           TEXT,
    domain         TEXT,
    sector         TEXT,
    discovered_via TEXT,              -- the search term, or "panel"
    in_panel       INTEGER NOT NULL DEFAULT 0,  -- baseline panel for the demand index
    first_seen     TEXT,
    last_fetched   TEXT,
    last_error     TEXT,
    PRIMARY KEY (source, slug)
);

-- Spec v2: daily record of the baseline panel's postings (demand index input)
CREATE TABLE IF NOT EXISTS posting_snapshots (
    day             TEXT NOT NULL,    -- YYYY-MM-DD (UTC)
    source          TEXT NOT NULL,
    company_slug    TEXT NOT NULL,
    posting_id      TEXT NOT NULL,
    title           TEXT,
    employment_type TEXT,
    published_at    TEXT,
    language        TEXT,
    skills_json     TEXT,
    PRIMARY KEY (day, source, posting_id)
);
CREATE INDEX IF NOT EXISTS posting_snapshots_day ON posting_snapshots(day);

-- Spec v2: Researcher briefs, one current brief per lead (spec §5.3)
CREATE TABLE IF NOT EXISTS briefs (
    lead_id    TEXT PRIMARY KEY,
    json       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Spec v2: versioned documents with approval state (profile, services); see versions.py
CREATE TABLE IF NOT EXISTS doc_versions (
    name        TEXT NOT NULL,          -- profile | services
    version     INTEGER NOT NULL,
    json        TEXT NOT NULL,
    status      TEXT NOT NULL,          -- draft | approved | superseded
    author      TEXT NOT NULL,          -- agent | user
    provenance  TEXT,                   -- JSON {model, prompt_version, input_ref, ts}
    checks      TEXT,                   -- JSON [{name, passed, note}]
    created_at  TEXT NOT NULL,
    approved_at TEXT,
    PRIMARY KEY (name, version)
);
"""

# Columns added after the first deploy. SQLite can't add columns in CREATE TABLE IF NOT
# EXISTS, so they are added here when missing (additive only: never drop or rename).
MIGRATIONS = {
    "leads": [
        ("source_posting_id", "TEXT"),   # e.g. recruitee:2823928
        ("company_slug", "TEXT"),
        ("department", "TEXT"),
        ("location", "TEXT"),
        ("remote", "INTEGER"),           # 1 remote, 0 not, NULL unknown
        ("employment_type", "TEXT"),
        ("language", "TEXT"),            # nl | en
        ("published_at", "TEXT"),
        ("description", "TEXT"),
        ("apply_url", "TEXT"),
        ("skills_json", "TEXT"),
        ("research_error", "TEXT"),      # last Researcher failure, if any
    ],
    "drafts": [
        ("language", "TEXT"),            # nl | en
        ("round", "INTEGER"),            # writer rounds used (1 + revisions)
        ("forwardable_blurb", "TEXT"),
        ("claims_json", "TEXT"),
        ("hook_source_url", "TEXT"),
        ("service_id", "TEXT"),
        ("final_text", "TEXT"),          # what the freelancer copied (after their edits)
        ("copied_at", "TEXT"),
        ("kind", "TEXT"),                # initial | followup (spec v2 Tracker: max one follow-up)
    ],
}
POST_MIGRATION_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS leads_source_posting ON leads(source_posting_id) "
    "WHERE source_posting_id IS NOT NULL",
)


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
        for table, columns in MIGRATIONS.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, sqltype in columns:
                if name not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}")
        for sql in POST_MIGRATION_SQL:
            conn.execute(sql)
        conn.commit()
