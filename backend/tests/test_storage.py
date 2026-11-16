"""Storage layer: SQLite schema, events log, wiki files, seeding, queue and RealStore."""

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import REPO_ROOT
from app.mock_store import NotFound
from app.storage import RealStore
from app.storage import db, events, queue, seed, wiki

FIXTURES = REPO_ROOT / "fixtures"
SEED_DIR = REPO_ROOT / "data"


def fixture(name: str) -> dict:
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    data.pop("_sample", None)
    return data


def assert_same_shape(actual, expected, path="$"):
    """Same keys at every level (dicts), and list items shaped like the fixture's first item."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected object, got {type(actual).__name__}"
        assert set(actual) == set(expected), f"{path}: keys {sorted(actual)} != {sorted(expected)}"
        for k in expected:
            assert_same_shape(actual[k], expected[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected list"
        if expected and actual:
            for i, item in enumerate(actual):
                assert_same_shape(item, expected[0], f"{path}[{i}]")


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def store(tmp_path) -> RealStore:
    s = RealStore(tmp_path / "data", SEED_DIR)
    yield s
    s.close()


def lead_input(n: int, **over) -> dict:
    base = fixture("leads")["leads"][n]
    base = {k: v for k, v in base.items() if k != "id"}
    base.update(over)
    return base


def draft_input(lead_id: str, **over) -> dict:
    d = dict(fixture("drafts")["drafts"][0])
    d.pop("id")
    d["lead_id"] = lead_id
    d.update(over)
    return d


# db ------------------------------------------------------------------------
def test_schema_creates_every_table_in_wal_mode(tmp_path):
    conn = db.connect(tmp_path / "d")
    db.init_schema(conn)
    db.init_schema(conn)  # idempotent
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"settings", "docs", "leads", "drafts", "replies", "deadlines", "queue", "metric_snapshots"} <= tables
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert (tmp_path / "d" / "app.db").exists()
    assert isinstance(conn.execute("SELECT 1 AS x").fetchone(), sqlite3.Row)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
    assert {"dedup_key", "verdict_note", "score", "fetched"} <= cols
    q = {r["name"]: r for r in conn.execute("PRAGMA table_info(queue)")}
    assert q["priority"]["dflt_value"] == "'normal'" and q["status"]["dflt_value"] == "'pending'"
    pk = [r["name"] for r in conn.execute("PRAGMA table_info(metric_snapshots)") if r["pk"]]
    assert pk == ["week", "name", "segment"]
    assert isinstance(db.WRITE_LOCK, type(threading.Lock()))


def test_unique_constraints_on_leads(tmp_path):
    conn = db.connect(tmp_path)
    db.init_schema(conn)
    conn.execute("INSERT INTO leads (id, nickname, dedup_key) VALUES ('a', 'x', 'k1')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO leads (id, nickname, dedup_key) VALUES ('b', 'x', 'k2')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO leads (id, nickname, dedup_key) VALUES ('c', 'y', 'k1')")


# events --------------------------------------------------------------------
def test_events_append_and_read_with_sequence(tmp_path):
    e1 = events.append_event(tmp_path, "seed_added", "user", "lead:x", {"url": "https://a"})
    e2 = events.append_event(tmp_path, "lead_found", "agent", "lead:y", {})
    e3 = events.append_event(tmp_path, "reply_received", "world", "lead:y", {"note": "hi"})
    assert [e1["id"], e2["id"], e3["id"]] == ["ev_0001", "ev_0002", "ev_0003"]
    assert e1["ts"].endswith("Z") and datetime.fromisoformat(e1["ts"].replace("Z", "+00:00"))
    assert set(e1) == {"id", "ts", "type", "actor", "subject", "payload"}
    assert events.read_events(tmp_path) == [e1, e2, e3]
    assert events.read_events(tmp_path, since_id="ev_0001") == [e2, e3]
    assert events.read_events(tmp_path, since_id="ev_0003") == []
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["ev_0001", "ev_0002", "ev_0003"]


def test_events_never_rewrite_and_continue_existing_sequence(tmp_path):
    path = tmp_path / "events.jsonl"
    existing = '{"id":"ev_0192","ts":"2026-11-20T14:02:00Z","type":"draft_edited","actor":"user","subject":"lead:p","payload":{}}\n'
    path.write_text(existing)
    ev = events.append_event(tmp_path, "draft_approved", "user", "lead:p", {})
    assert ev["id"] == "ev_0193"
    assert path.read_text().startswith(existing)
    # A file edited behind our back (different size) is rescanned, not trusted from cache
    with path.open("a") as fh:
        fh.write('{"id":"ev_0500","ts":"x","type":"lead_found","actor":"agent","subject":"s","payload":{}}\n')
    assert events.append_event(tmp_path, "lead_found", "agent", "s", {})["id"] == "ev_0501"


def test_events_ids_keep_numeric_order_past_9999(tmp_path):
    (tmp_path / "events.jsonl").write_text(
        '{"id":"ev_9999","ts":"x","type":"lead_found","actor":"agent","subject":"s","payload":{}}\n'
    )
    ev = events.append_event(tmp_path, "lead_found", "agent", "s", {})
    assert ev["id"] == "ev_10000"
    assert [e["id"] for e in events.read_events(tmp_path, since_id="ev_9999")] == ["ev_10000"]


def test_events_torn_last_line_is_skipped_not_repaired(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"id":"ev_0001","ts":"x","type":"lead_found","actor":"agent","subject":"s","payload":{}}\n{"id":"ev_00')
    ev = events.append_event(tmp_path, "lead_found", "agent", "s", {})
    assert ev["id"] == "ev_0002"
    assert path.read_text().startswith('{"id":"ev_0001"')
    assert [e["id"] for e in events.read_events(tmp_path)] == ["ev_0001", "ev_0002"]


@pytest.mark.parametrize(
    "type_, actor, subject, payload",
    [
        ("made_up", "user", "goal", {}),
        ("seed_added", "robot", "goal", {}),
        ("seed_added", "user", "", {}),
        ("seed_added", "user", "goal", ["not", "a", "dict"]),
    ],
)
def test_events_validate_type_actor_subject_payload(tmp_path, type_, actor, subject, payload):
    with pytest.raises(ValueError):
        events.append_event(tmp_path, type_, actor, subject, payload)
    assert not (tmp_path / "events.jsonl").exists()


def test_events_all_documented_types_accepted(tmp_path):
    for t in events.EVENT_TYPES:
        events.append_event(tmp_path, t, "agent", "goal", {})
    assert len(events.read_events(tmp_path)) == 28


def test_events_read_rejects_bad_since_and_handles_missing_file(tmp_path):
    assert events.read_events(tmp_path) == []
    with pytest.raises(ValueError):
        events.read_events(tmp_path, since_id="nope")


def test_events_thread_safe_unique_sequence(tmp_path):
    def worker():
        for _ in range(25):
            events.append_event(tmp_path, "lead_found", "agent", "s", {})

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [e["id"] for e in events.read_events(tmp_path)]
    assert len(ids) == 200
    assert ids == [f"ev_{i:04d}" for i in range(1, 201)]


# wiki ----------------------------------------------------------------------
def test_wiki_read_write_pages_and_goal(tmp_path):
    assert wiki.write_page(tmp_path, "wiki/persona.md", "# Persona\n") == "wiki/persona.md"
    assert wiki.read_page(tmp_path, "wiki/persona.md") == "# Persona\n"
    assert wiki.write_page(tmp_path, "wiki/clients/acme.md", "acme") == "wiki/clients/acme.md"
    assert wiki.read_page(tmp_path, "wiki/clients/acme.md") == "acme"
    assert wiki.write_page(tmp_path, "goal.md", "# Goal\n") == "goal.md"
    assert (tmp_path / "goal.md").read_text() == "# Goal\n"
    assert not list((tmp_path / "wiki").glob(".tmp-*"))
    with pytest.raises(NotFound):
        wiki.read_page(tmp_path, "wiki/missing.md")


@pytest.mark.parametrize(
    "rel",
    [
        "../secrets.md", "wiki/../goal.md", "wiki/../../x.md", "/etc/passwd", "/tmp/x.md",
        "wiki\\..\\x.md", "C:/x.md", "", "   ", "./wiki/a.md", "wiki/./a.md", "raw/2026-cv.md",
        "AGENTS.md", "events.jsonl", "wiki/page.txt", "wiki/a.md\x00", "wiki/", "persona.md",
        "wiki//x.md", "sub/goal.md",
    ],
)
def test_wiki_rejects_escaping_or_disallowed_paths(tmp_path, rel):
    with pytest.raises(ValueError):
        wiki.write_page(tmp_path, rel, "x")
    with pytest.raises(ValueError):
        wiki.read_page(tmp_path, rel)


def test_wiki_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.md").write_text("secret")
    data = tmp_path / "data"
    (data / "wiki").mkdir(parents=True)
    (data / "wiki" / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        wiki.read_page(data, "wiki/link/x.md")
    with pytest.raises(ValueError):
        wiki.write_page(data, "wiki/link/x.md", "overwrite")
    assert (outside / "x.md").read_text() == "secret"


def test_wiki_log_is_append_only(tmp_path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("# Log")  # no trailing newline
    line = wiki.append_log(tmp_path, "ingest", "Read the\nresume")
    assert line.endswith(" · ingest · Read the resume")
    datetime.strptime(line.split(" · ")[0], "%Y-%m-%d %H:%M")
    wiki.append_log(tmp_path, "lint", "ok")
    lines = (tmp_path / "wiki" / "log.md").read_text().splitlines()
    assert lines[0] == "# Log" and lines[1] == line and lines[2].endswith("· lint · ok")
    with pytest.raises(ValueError):
        wiki.write_page(tmp_path, "wiki/log.md", "rewritten")
    with pytest.raises(ValueError):
        wiki.append_log(tmp_path, "bad kind!", "x")
    with pytest.raises(ValueError):
        wiki.append_log(tmp_path, "run", "   ")


def test_save_raw_names_sanitizes_and_never_overwrites(tmp_path):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    a = wiki.save_raw(tmp_path, "My Resume (final).PDF", b"one")
    b = wiki.save_raw(tmp_path, "My Resume (final).PDF", b"two")
    c = wiki.save_raw(tmp_path, "My Resume (final).PDF", b"three")
    assert a == f"raw/{today}-my-resume-final.pdf"
    assert b == f"raw/{today}-my-resume-final-2.pdf"
    assert c == f"raw/{today}-my-resume-final-3.pdf"
    assert (tmp_path / a).read_bytes() == b"one" and (tmp_path / b).read_bytes() == b"two"
    evil = wiki.save_raw(tmp_path, "../../etc/passwd", b"x")
    assert evil == f"raw/{today}-passwd"
    win = wiki.save_raw(tmp_path, "C:\\Users\\me\\cv.docx", b"x")
    assert win == f"raw/{today}-cv.docx"
    assert wiki.save_raw(tmp_path, "", b"x") == f"raw/{today}-file"
    assert wiki.save_raw(tmp_path, ".bashrc", b"x").startswith(f"raw/{today}-file")
    assert all(p.parent == tmp_path / "raw" for p in (tmp_path / "raw").iterdir())
    with pytest.raises(ValueError):
        wiki.save_raw(tmp_path, "x.txt", "not bytes")


# seed ----------------------------------------------------------------------
def test_seed_copies_repo_data_once(tmp_path):
    data = tmp_path / "data"
    assert seed.ensure_seeded(data, SEED_DIR) is True
    for rel in ("goal.md", "AGENTS.md", "benchmarks.json", "events.jsonl", "wiki/index.md",
                "wiki/lessons.md", "wiki/log.md"):
        assert (data / rel).read_bytes() == (SEED_DIR / rel).read_bytes(), rel
    assert (data / "raw").is_dir()
    assert seed.ensure_seeded(data, SEED_DIR) is False


def test_seed_never_overwrites_existing_files(tmp_path):
    data = tmp_path / "data"
    (data / "wiki").mkdir(parents=True)
    (data / "wiki" / "index.md").write_text("mine")
    (data / "AGENTS.md").write_text("mine too")
    assert seed.ensure_seeded(data, SEED_DIR) is True
    assert (data / "wiki" / "index.md").read_text() == "mine"
    assert (data / "AGENTS.md").read_text() == "mine too"
    assert (data / "wiki" / "lessons.md").exists()
    # Once goal.md exists nothing is copied again, even missing files
    (data / "wiki" / "lessons.md").unlink()
    (data / "goal.md").write_text("edited goal")
    assert seed.ensure_seeded(data, SEED_DIR) is False
    assert not (data / "wiki" / "lessons.md").exists()
    assert (data / "goal.md").read_text() == "edited goal"


def test_seed_requires_a_seed_folder(tmp_path):
    with pytest.raises(FileNotFoundError):
        seed.ensure_seeded(tmp_path / "data", tmp_path / "nothing")


# queue ---------------------------------------------------------------------
@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path)
    db.init_schema(c)
    yield c
    c.close()


def test_queue_urgent_first_then_oldest(conn):
    a = queue.enqueue(conn, "score", "ld_1")
    b = queue.enqueue(conn, "triage", "rp_1", priority="urgent", payload={"x": 1})
    c = queue.enqueue(conn, "score", "ld_2")
    d = queue.enqueue(conn, "triage", "rp_2", priority="urgent")
    assert [i["id"] for i in queue.pending(conn)] == [b, d, a, c]
    first = queue.take_next(conn)
    assert first["id"] == b and first["status"] == "taken" and first["taken_at"]
    assert first["payload"] == {"x": 1} and first["kind"] == "triage" and first["ref"] == "rp_1"
    assert [queue.take_next(conn)["id"] for _ in range(3)] == [d, a, c]
    assert queue.take_next(conn) is None
    assert queue.pending(conn) == []


def test_queue_complete_ok_and_failed(conn):
    a = queue.enqueue(conn, "send", "dr_1")
    b = queue.enqueue(conn, "send", "dr_2")
    queue.take_next(conn)
    done = queue.complete(conn, a, ok=True)
    assert done["status"] == "done" and done["done_at"] and done["error"] is None
    failed = queue.complete(conn, b, ok=False, error="smtp down")
    assert failed["status"] == "failed" and failed["error"] == "smtp down"
    with pytest.raises(ValueError):
        queue.complete(conn, a, ok=True)
    with pytest.raises(NotFound):
        queue.complete(conn, 9999, ok=True)


def test_queue_rejects_bad_priority_and_kind(conn):
    with pytest.raises(ValueError):
        queue.enqueue(conn, "send", "x", priority="whenever")
    with pytest.raises(ValueError):
        queue.enqueue(conn, "", "x")


def test_queue_take_is_atomic_across_connections(tmp_path):
    setup = db.connect(tmp_path)
    db.init_schema(setup)
    for i in range(60):
        queue.enqueue(setup, "score", f"ld_{i}")
    taken, errors = [], []

    def worker():
        c = db.connect(tmp_path)  # separate connection, same file
        try:
            while (item := queue.take_next(c)) is not None:
                taken.append(item["id"])
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(taken) == list(range(1, 61))  # every row exactly once
    setup.close()


def test_queue_take_does_not_double_claim_with_raw_racing_update(tmp_path):
    """Two connections running the claim UPDATE: the second sees no pending row."""
    c1, c2 = db.connect(tmp_path), db.connect(tmp_path)
    db.init_schema(c1)
    queue.enqueue(c1, "send", "dr_1")
    assert queue.take_next(c1)["ref"] == "dr_1"
    assert queue.take_next(c2) is None
    c1.close()
    c2.close()


# RealStore: empty state ----------------------------------------------------
def test_new_store_seeds_and_has_empty_valid_shapes(store, tmp_path):
    assert (tmp_path / "data" / "goal.md").exists()
    assert (tmp_path / "data" / "app.db").exists()

    persona = store.get("persona")
    assert set(persona) == set(fixture("persona"))
    assert persona["sources"] == [] and persona["skills"] == [] and persona["name"] == ""

    fit = store.get("fit")
    assert_same_shape(fit, fixture("fit"))
    assert fit["fits"] == [] and fit["rate"]["ask"] is None
    assert fit["rate"]["floor"] is None  # owned by the approved profile; none yet
    assert fit["rate"]["currency"] == "USD" and fit["rate"]["unit"] == "hour"

    assert store.get("offers") == {"offers": []}

    metrics = store.get("metrics")
    assert set(metrics) == set(fixture("metrics"))
    assert metrics["headline"] == [] and metrics["channels"] == [] and metrics["learning"] == []
    assert metrics["guardrails"] == {k: 0 for k in fixture("metrics")["guardrails"]}
    assert metrics["week"].startswith(str(datetime.now(timezone.utc).isocalendar().year))

    state = store.get("state")
    assert_same_shape(state, fixture("state"))
    assert state["today"] == {"new_leads": 0, "drafts_pending": 0, "deadlines_this_week": 0, "replies_unread": 0}
    assert state["mode"] == "real" and state["last_checkin_at"] is None

    pipeline = store.get("pipeline")
    assert [c["name"] for c in pipeline["columns"]] == ["Drafted", "Sent", "Replied", "Active", "Done"]
    assert all(c["items"] == [] for c in pipeline["columns"]) and pipeline["deadlines"] == []

    assert store.get("leads") == {"leads": []}
    assert store.get("drafts") == {"drafts": []}
    for name in ("state", "persona", "fit", "offers", "leads", "drafts", "pipeline", "metrics"):
        assert "_sample" not in store.get(name)
    with pytest.raises(KeyError):
        store.get("nope")


def test_reopening_keeps_data(tmp_path):
    s1 = RealStore(tmp_path / "d", SEED_DIR)
    s1.update_persona({"name": "Sam"})
    s1.close()
    s2 = RealStore(tmp_path / "d", SEED_DIR)
    assert s2.get("persona")["name"] == "Sam"
    s2.close()


# RealStore: docs shaped like fixtures ---------------------------------------
@pytest.mark.parametrize("name", ["persona", "fit", "offers", "metrics"])
def test_set_doc_round_trips_fixture_shapes(store, name):
    doc = json.loads((FIXTURES / f"{name}.json").read_text())  # includes _sample: stripped on save
    out = store.set_doc(name, doc)
    assert out == fixture(name)
    assert store.get(name) == fixture(name)
    assert_same_shape(store.get(name), fixture(name))


def test_set_doc_rejects_unknown_names(store):
    with pytest.raises(ValueError):
        store.set_doc("leads", {})
    with pytest.raises(ValueError):
        store.set_doc("persona", ["x"])


def test_partial_docs_are_filled_to_valid_shapes(store):
    store.set_doc("fit", {"fits": [], "rate": {"ask": 120}})
    fit = store.get("fit")
    assert set(fit["rate"]) == set(fixture("fit")["rate"]) and fit["rate"]["ask"] == 120
    assert fit["rate"]["floor"] is None
    store.set_doc("metrics", {"guardrails": {"unapproved_sends": 0}})
    assert set(store.get("metrics")["guardrails"]) == set(fixture("metrics")["guardrails"])


def test_settings_round_trip(store):
    assert store.get_setting("missing") is None
    assert store.get_setting("missing", 5) == 5
    store.set_setting("last_checkin_at", "2026-11-15T08:00:00Z")
    store.set_setting("weights", {"hn": 0.5})
    assert store.get_setting("weights") == {"hn": 0.5}
    store.set_setting("weights", {"hn": 0.7})
    assert store.get_setting("weights") == {"hn": 0.7}
    assert store.get("state")["last_checkin_at"] == "2026-11-15T08:00:00Z"


# RealStore: persona ----------------------------------------------------------
def test_update_persona_filters_fields_and_logs_event(store):
    out = store.update_persona({"name": "Sam", "skills": ["Python"], "years": 5, "sources": ["hack"], "evil": 1})
    assert out["name"] == "Sam" and out["skills"] == ["Python"] and out["years"] == 5
    assert out["sources"] == [] and "evil" not in out
    assert set(out) == set(fixture("persona"))
    ev = events.read_events(store.data_dir)[-1]
    assert ev["type"] == "persona_updated" and ev["actor"] == "user" and ev["subject"] == "persona"
    assert ev["payload"]["fields"] == {"name": "Sam", "skills": ["Python"], "years": 5}
    state = store.get("state")
    assert state["profile"]["name"] == "Sam"


def test_state_headline_from_persona(store):
    store.set_doc("persona", json.loads((FIXTURES / "persona.json").read_text()))
    assert store.get("state")["profile"] == {"name": "Sam Verhoeven", "headline": "Project manager · 10 yrs · Amsterdam, Netherlands"}


def test_add_cv_saves_raw_and_appends_source(store):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    p1 = store.add_cv("Resume.pdf", b"%PDF-1")
    assert p1["sources"] == [f"raw/{today}-resume.pdf"]
    assert (store.data_dir / "raw" / f"{today}-resume.pdf").read_bytes() == b"%PDF-1"
    p2 = store.add_cv("Resume.pdf", b"%PDF-2")
    assert p2["sources"] == [f"raw/{today}-resume.pdf", f"raw/{today}-resume-2.pdf"]
    assert (store.data_dir / "raw" / f"{today}-resume.pdf").read_bytes() == b"%PDF-1"
    assert set(p2) == set(fixture("persona"))
    evs = [e for e in events.read_events(store.data_dir) if e["type"] == "persona_updated"]
    assert [e["payload"]["source"] for e in evs] == p2["sources"]


# RealStore: leads ------------------------------------------------------------
def test_upsert_leads_matches_fixture_shape_and_skips_duplicates(store):
    inserted = store.upsert_leads([lead_input(i) for i in range(4)])
    assert len(inserted) == 4
    for lead in inserted:
        assert set(lead) == set(fixture("leads")["leads"][0])
        assert lead["id"].startswith("ld_") and isinstance(lead["fetched"], bool)
    got = store.leads()
    assert_same_shape(got, fixture("leads"))
    assert set(got) == {"leads"}
    assert [l["score"] for l in got["leads"]] == [92, 87, 71, 64]  # ranked
    assert [l["nickname"] for l in got["leads"]] == ["payments-api", "saas-agent", "data-pipeline", "react-rebuild"]
    # Same URLs again (one with a trailing slash and a fragment) -> skipped
    again = store.upsert_leads([lead_input(0, url="https://news.ycombinator.com/item?id=0#x"), lead_input(1)])
    assert again == []
    # Different URL but nickname taken -> new unique nickname
    extra = store.upsert_leads([lead_input(0, url="https://example.com/other")])
    assert extra[0]["nickname"] == "payments-api-2"
    # Explicit dedup_key wins
    store.upsert_leads([lead_input(2, url="https://x.test/1", dedup_key="K")])
    assert store.upsert_leads([lead_input(2, url="https://x.test/2", dedup_key="K")]) == []
    found = [e for e in events.read_events(store.data_dir) if e["type"] == "lead_found"]
    assert len(found) == 6 and all(e["actor"] == "agent" for e in found)
    assert store.get("leads") == store.leads()


def test_upsert_leads_dedups_within_one_batch_and_validates_status(store):
    out = store.upsert_leads([lead_input(0), lead_input(0)])
    assert len(out) == 1
    with pytest.raises(ValueError):
        store.upsert_leads([lead_input(1, status="bogus")])
    assert len(store.leads()["leads"]) == 1  # the failed batch rolled back


def test_leads_filter_by_status(store):
    ids = [l["id"] for l in store.upsert_leads([lead_input(i) for i in range(3)])]
    store.set_verdict(ids[0], "skip", None)
    assert [l["id"] for l in store.leads("skip")["leads"]] == [ids[0]]
    assert len(store.leads("new")["leads"]) == 2
    assert store.leads("won") == {"leads": []}


def test_set_verdict_updates_lead_and_logs_event(store):
    lead = store.upsert_leads([lead_input(0)])[0]
    out = store.set_verdict(lead["id"], "useful", "Great fit")
    assert out["status"] == "useful" and out["verdict_note"] == "Great fit"
    assert set(out) == set(fixture("leads")["leads"][0]) | {"verdict_note"}
    ev = events.read_events(store.data_dir)[-1]
    assert ev["type"] == "lead_verdict" and ev["subject"] == "lead:payments-api"
    assert ev["payload"] == {"lead_id": lead["id"], "verdict": "useful", "previous": "new", "note": "Great fit"}
    out2 = store.set_verdict(lead["id"], "maybe", None)
    assert out2["status"] == "maybe" and out2["verdict_note"] == "Great fit"
    assert "note" not in events.read_events(store.data_dir)[-1]["payload"]
    with pytest.raises(ValueError):
        store.set_verdict(lead["id"], "won", None)


def test_set_verdict_unknown_lead_raises_not_found(store):
    with pytest.raises(NotFound):
        store.set_verdict("ld_nope", "useful", None)
    assert events.read_events(store.data_dir) == []


def test_add_seed_creates_unscored_lead_and_dedups(store):
    store.upsert_leads([lead_input(0)])
    lead = store.add_seed("https://example.com/jobs/42")
    assert set(lead) == set(fixture("leads")["leads"][0])
    assert lead["status"] == "new" and lead["source"] == "seed" and lead["fetched"] is False
    assert lead["score"] is None and lead["url"] == "https://example.com/jobs/42"
    assert lead["nickname"] == "pasted-link"
    assert store.leads()["leads"][0]["id"] == lead["id"]  # just-pasted shows first, like mock mode
    second = store.add_seed("https://example.com/jobs/43")
    assert second["nickname"] == "pasted-link-2"
    again = store.add_seed("https://EXAMPLE.com/jobs/42/")
    assert again["id"] == lead["id"]
    assert len(store.leads()["leads"]) == 3
    seeds = [e for e in events.read_events(store.data_dir) if e["type"] == "seed_added"]
    assert len(seeds) == 3 and seeds[-1]["payload"]["duplicate"] is True
    assert seeds[0]["payload"] == {"url": "https://example.com/jobs/42", "lead_id": lead["id"]}


# RealStore: drafts -----------------------------------------------------------
def test_add_draft_matches_fixture_shape_and_moves_lead(store):
    lead = store.upsert_leads([lead_input(0)])[0]
    draft = store.add_draft(draft_input(lead["id"], template_version="v3"))
    assert set(draft) == set(fixture("drafts")["drafts"][0])
    assert_same_shape(store.drafts(), fixture("drafts"))
    assert draft["status"] == "pending" and draft["nickname"] == "payments-api"
    assert draft["to"] == "[HIRING MANAGER EMAIL]" and draft["verifier"]["passed"] is True
    assert store.leads()["leads"][0]["status"] == "drafted"
    ev = events.read_events(store.data_dir)[-1]
    assert ev["type"] == "draft_created" and ev["actor"] == "agent"
    assert ev["payload"]["draft_id"] == draft["id"] and ev["payload"]["template"] == "v3"
    assert store.drafts("pending")["drafts"] == [draft]
    assert store.drafts("sent") == {"drafts": []}
    assert store.get("state")["today"]["drafts_pending"] == 1


def test_add_draft_defaults_and_validation(store):
    d = store.add_draft({"channel": "apply_link", "body": "hi", "subject": None})
    assert d["verifier"] == {"passed": False, "checks": []} and d["lead_id"] is None
    with pytest.raises(NotFound):
        store.add_draft({"lead_id": "ld_nope", "body": "x"})
    with pytest.raises(ValueError):
        store.add_draft({"channel": "sms", "body": "x"})
    with pytest.raises(ValueError):
        store.add_draft({"status": "maybe", "body": "x"})


def test_approve_draft_approves_and_enqueues_send(store):
    lead = store.upsert_leads([lead_input(0)])[0]
    draft = store.add_draft(draft_input(lead["id"], template_version="v3"))
    out = store.approve_draft(draft["id"], "Edited text")
    assert out["status"] == "approved" and out["body"] == "Edited text"
    assert out["approved_at"] and "sent_at" not in out
    assert set(out) == set(fixture("drafts")["drafts"][0]) | {"approved_at"}
    items = queue.pending(store.conn)
    assert len(items) == 1 and items[0]["kind"] == "send" and items[0]["ref"] == draft["id"]
    evs = events.read_events(store.data_dir)
    edited, approved = evs[-2], evs[-1]
    assert edited["type"] == "draft_edited" and edited["subject"] == "lead:payments-api"
    assert edited["payload"]["before"] == draft["body"] and edited["payload"]["after"] == "Edited text"
    assert edited["payload"]["template"] == "v3"
    assert approved["type"] == "draft_approved" and approved["payload"]["queue_id"] == items[0]["id"]


def test_approve_unchanged_text_logs_no_edit(store):
    draft = store.add_draft({"body": "Same", "channel": "email"})
    store.approve_draft(draft["id"], "Same")
    types = [e["type"] for e in events.read_events(store.data_dir)]
    assert "draft_edited" not in types and types[-1] == "draft_approved"


def test_approve_twice_fails_and_enqueues_once(store):
    draft = store.add_draft({"body": "x"})
    store.approve_draft(draft["id"], "x")
    with pytest.raises(ValueError, match="only a pending draft"):
        store.approve_draft(draft["id"], "x")
    with pytest.raises(ValueError, match="only an open draft"):
        store.reject_draft(draft["id"], "late")
    assert len(queue.pending(store.conn)) == 1
    with pytest.raises(ValueError):
        store.approve_draft(store.add_draft({"body": "y"})["id"], "  ")


def test_reject_draft(store):
    draft = store.add_draft({"body": "x"})
    out = store.reject_draft(draft["id"], "Too long")
    assert out["status"] == "rejected" and out["reject_note"] == "Too long"
    ev = events.read_events(store.data_dir)[-1]
    assert ev["type"] == "draft_rejected" and ev["payload"] == {"draft_id": draft["id"], "note": "Too long"}
    assert ev["subject"] == f"draft:{draft['id']}"
    with pytest.raises(ValueError):
        store.approve_draft(draft["id"], "x")
    assert queue.pending(store.conn) == []


def test_unknown_draft_ids_raise_not_found(store):
    with pytest.raises(NotFound):
        store.approve_draft("dr_nope", "x")
    with pytest.raises(NotFound):
        store.reject_draft("dr_nope", "x")


# RealStore: state and pipeline -----------------------------------------------
def test_state_and_pipeline_computed_from_tables(store):
    now = datetime.now(timezone.utc)
    leads = store.upsert_leads([lead_input(i) for i in range(4)])
    by_nick = {l["nickname"]: l for l in leads}
    store.add_draft(draft_input(by_nick["data-pipeline"]["id"]))
    sent_id = store.add_draft(draft_input(by_nick["react-rebuild"]["id"]))["id"]
    with store.conn:
        store.conn.execute("UPDATE drafts SET status='sent', sent_at=? WHERE id=?",
                           (iso(now - timedelta(days=9)), sent_id))
        store.conn.execute("UPDATE leads SET status='sent' WHERE nickname='react-rebuild'")
        store.conn.execute("UPDATE leads SET status='replied' WHERE nickname='payments-api'")
        store.conn.execute("UPDATE leads SET status='won' WHERE nickname='saas-agent'")
        store.conn.execute("INSERT INTO replies (id, lead_id, received_at, body, label) VALUES "
                           "('rp_1', ?, ?, 'Let us talk', NULL)", (by_nick["payments-api"]["id"], iso(now)))
        store.conn.execute("INSERT INTO deadlines VALUES ('dl_1', ?, 'saas-agent', 'Milestone 1', ?, 0)",
                           (by_nick["saas-agent"]["id"], iso(now + timedelta(days=6))))
        store.conn.execute("INSERT INTO deadlines VALUES ('dl_2', ?, 'payments-api', 'Intro call', ?, 0)",
                           (by_nick["payments-api"]["id"], iso(now + timedelta(days=3))))
        store.conn.execute("INSERT INTO deadlines VALUES ('dl_3', NULL, 'x', 'Far away', ?, 0)",
                           (iso(now + timedelta(days=30)),))
        store.conn.execute("INSERT INTO deadlines VALUES ('dl_4', NULL, 'x', 'Done one', ?, 1)", (iso(now),))
    store.add_seed("https://example.com/new")

    pipeline = store.get("pipeline")
    assert_same_shape(pipeline, fixture("pipeline"))
    cols = {c["name"]: c["items"] for c in pipeline["columns"]}
    assert cols["Drafted"] == [{"nickname": "data-pipeline", "note": "Waiting for approval"}]
    assert cols["Sent"] == [{"nickname": "react-rebuild", "note": "No reply, 9 days"}]
    assert cols["Replied"] == [{"nickname": "payments-api", "note": "Reply received"}]
    assert cols["Active"][0]["nickname"] == "saas-agent" and "Milestone 1" in cols["Active"][0]["note"]
    assert cols["Done"] == []
    assert [d["what"] for d in pipeline["deadlines"]] == ["Intro call", "Milestone 1", "Far away"]
    assert pipeline["deadlines"][0]["due"] == (now + timedelta(days=3)).strftime("%Y-%m-%d")

    state = store.get("state")
    assert_same_shape(state, fixture("state"))
    assert state["today"] == {"new_leads": 1, "drafts_pending": 1, "deadlines_this_week": 2, "replies_unread": 1}


# RealStore: reset --------------------------------------------------------------
def test_reset_wipes_db_and_events_then_reseeds(store):
    store.upsert_leads([lead_input(0)])
    store.update_persona({"name": "Sam"})
    assert events.read_events(store.data_dir)
    (store.data_dir / "goal.md").unlink()
    store.reset()
    assert store.leads() == {"leads": []}
    assert store.get("persona")["name"] == ""
    assert events.read_events(store.data_dir) == []
    assert (store.data_dir / "goal.md").exists()
    # Writes work after reset and the sequence restarts
    store.add_seed("https://example.com/a")
    assert events.read_events(store.data_dir)[0]["id"] == "ev_0001"


def test_reset_keeps_wiki_and_raw_and_touches_only_data_dir(store, tmp_path):
    neighbour = tmp_path / "events.jsonl"
    neighbour.write_text("keep me\n")
    wiki.write_page(store.data_dir, "wiki/persona.md", "kept")
    store.add_cv("cv.pdf", b"x")
    store.reset()
    assert neighbour.read_text() == "keep me\n"
    assert wiki.read_page(store.data_dir, "wiki/persona.md") == "kept"
    assert list((store.data_dir / "raw").iterdir())
    assert (SEED_DIR / "goal.md").exists()


def test_reset_refuses_when_data_dir_is_the_seed_folder(tmp_path):
    seed_copy = tmp_path / "seed"
    seed.ensure_seeded(seed_copy, SEED_DIR)
    s = RealStore(seed_copy, seed_copy)
    s.add_seed("https://example.com/a")
    with pytest.raises(ValueError):
        s.reset()
    assert events.read_events(seed_copy)  # untouched
    s.close()


# Parity with MockStore ------------------------------------------------------------
def test_public_methods_cover_mock_store():
    from app.mock_store import MockStore

    public = {n for n in dir(MockStore) if not n.startswith("_")}
    assert public <= {n for n in dir(RealStore) if not n.startswith("_")}
