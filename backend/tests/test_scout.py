"""Scout (spec v2 §6.1): Oxylabs search, Recruitee boards, filters, tags, snapshots.

Fictional companies and postings; no network (httpx.MockTransport and a fake search).
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import scout
from app.config import REPO_ROOT, Settings
from app.main import create_app
from app.providers.search import OxylabsSearch, ProviderUnavailable
from app.sources import recruitee
from app.storage import RealStore, companies, db
from app.tagging import detect_language, tag_skills

NOW = datetime.now(timezone.utc)


def ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S UTC")


def offer(id, title, days_ago=3, country="NL", remote=False, lang="en", desc="We build data pipelines in Python and dbt.",
          etype="fulltime_permanent", company="Tulip Data", req="<ul><li>SQL</li></ul>"):
    return {"id": id, "slug": f"job-{id}", "title": title, "department": "Data", "city": "Amsterdam",
            "country_code": country, "location": "Amsterdam, Noord-Holland, Netherlands", "remote": remote,
            "hybrid": True, "employment_type_code": etype, "published_at": ts(days_ago), "status": "published",
            "description": f"<p>{desc}</p>", "requirements": req,
            "careers_url": f"https://tulipdata.recruitee.com/o/job-{id}",
            "careers_apply_url": f"https://tulipdata.recruitee.com/o/job-{id}/c/new",
            "company_name": company, "category_code": "it", "translations": {lang: {}},
            "mailbox_email": "j.jansen@example.com"}


BOARDS = {
    "tulipdata": [
        offer(1, "Senior Data Engineer"),
        offer(2, "Analytics Engineer", days_ago=45),                          # too old
        offer(3, "Data Engineer (Berlin)", country="DE"),                     # not NL, not remote
        offer(4, "Remote ML Engineer", country="DE", remote=True),            # remote: kept
        offer(5, "Data-analist", lang="nl", desc="Wij zoeken een data-analist met ervaring in Power BI en SQL."),
        offer(6, "WordPress beheerder", desc="CMS maintenance for our websites"),  # exclusion
        offer(7, "Office manager", desc="Plan the office and welcome visitors", req=""),  # no skill match
    ],
    "grachtsoft": [offer(10, "Backend Developer", desc="Python, FastAPI and Kubernetes", company="Grachtsoft")],
    "brokenboard": None,  # 404
}


class FakeSearch:
    def __init__(self, urls_by_term=None):
        self.queries = []
        self.urls = urls_by_term or {}

    def search(self, query, count=10, locale="NL"):
        self.queries.append((query, count, locale))
        urls = next((u for t, u in self.urls.items() if f'"{t}"' in query), None)
        if urls is None:
            urls = ["https://tulipdata.recruitee.com/o/job-1", "https://grachtsoft.recruitee.com/",
                    "https://www.recruitee.com/blog/x", "https://brokenboard.recruitee.com/o/a",
                    "https://linkedin.com/in/some-person"]
        return [{"url": u, "title": "", "snippet": "", "published_at": None} for u in urls]


def board_client():
    def handler(request: httpx.Request) -> httpx.Response:
        slug = request.url.host.split(".")[0]
        assert request.url.path == "/api/offers/"
        offers = BOARDS.get(slug)
        if offers is None:
            return httpx.Response(404, json={"error": "Not Found"})
        return httpx.Response(200, json={"offers": offers})
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture()
def store(tmp_path):
    s = RealStore(tmp_path / "data", REPO_ROOT / "data")
    s.update_persona({"skills": ["Python", "dbt"], "avoids": "CMS maintenance, unpaid trials"})
    yield s
    s.close()


def settings(tmp_path, key="k"):
    return Settings(mock_mode=False, data_dir=tmp_path / "data", fixtures_dir=REPO_ROOT / "fixtures", mcp_token="t",
                    cors_origins=(), mock_stream_delay=0, web_dist=tmp_path / "x", landing_file=tmp_path / "y.html",
                    oxylabs_key=key)


# Oxylabs search ----------------------------------------------------------------
def test_oxylabs_sends_bearer_and_only_documented_params():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"state": "done", "results": [
            {"title": "Jobs", "outline": ["a", "b"], "url": "https://x.recruitee.com/", "metadata": {"position": 1}},
            {"title": "no url", "outline": []}]})

    s = OxylabsSearch("KEY", httpx.Client(transport=httpx.MockTransport(handler)))
    out = s.search("site:recruitee.com data", count=50, locale="nl")
    assert seen["auth"] == "Bearer KEY"
    assert seen["body"] == {"query": "site:recruitee.com data", "max_results": 20, "location": "NL"}
    assert out == [{"url": "https://x.recruitee.com/", "title": "Jobs", "snippet": "a b", "published_at": None}]


def test_oxylabs_without_key_or_with_bad_key_is_readable():
    with pytest.raises(ProviderUnavailable, match="OXYLABS_WEB_API_KEY"):
        OxylabsSearch("")
    s = OxylabsSearch("bad", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    with pytest.raises(ProviderUnavailable, match="401"):
        s.search("x")


def test_oxylabs_retries_then_gives_up():
    calls = []
    s = OxylabsSearch("k", httpx.Client(transport=httpx.MockTransport(
        lambda r: calls.append(1) or httpx.Response(503))), retries=2, sleep=lambda _s: None)
    with pytest.raises(ProviderUnavailable, match="503"):
        s.search("x")
    assert len(calls) == 3


# Recruitee ---------------------------------------------------------------------
def test_slug_from_url_skips_recruitee_own_pages_and_people():
    assert recruitee.slug_from_url("https://tulipdata.recruitee.com/o/job-1") == "tulipdata"
    assert recruitee.slug_from_url("https://www.recruitee.com/blog") is None
    assert recruitee.slug_from_url("https://linkedin.com/in/someone") is None


def test_parse_offer_maps_fields_and_drops_personal_mailbox():
    lead = recruitee.parse_offer(offer(1, "Senior Data Engineer"), "tulipdata")
    assert lead["source_posting_id"] == lead["dedup_key"] == "recruitee:1"
    assert lead["apply_url"].endswith("/c/new") and lead["country_code"] == "NL"
    assert lead["published_at"].endswith("Z") and lead["language"] == "en"
    assert "<p>" not in lead["description"] and "SQL" in lead["description"]
    assert "j.jansen" not in json.dumps(lead)


def test_fetch_postings_backs_off_on_429():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200, json={"offers": [offer(1, "X")]})

    out = recruitee.fetch_postings(httpx.Client(transport=httpx.MockTransport(handler)), "tulipdata",
                                   sleep=lambda _s: None)
    assert len(out) == 1 and len(calls) == 2


def test_fetch_postings_refuses_bad_slug():
    with pytest.raises(recruitee.RecruiteeError):
        recruitee.fetch_postings(board_client(), "evil.example.com/")


# Tagging -----------------------------------------------------------------------
def test_language_detection():
    assert detect_language("Wij zoeken een collega met ervaring in data voor ons team") == "nl"
    assert detect_language("We are looking for an engineer with experience in data") == "en"
    assert detect_language("whatever", hint="nl") == "nl"


def test_skill_tags_use_word_boundaries_and_own_skills():
    tags = tag_skills("Senior engineer: PySpark, dbt, C# and .NET; Golang a plus. Uses Javascripting?", ["Hotwire"])
    assert {"Spark", "dbt", "C#/.NET", "Go"} <= set(tags)
    assert "JavaScript" not in tags
    assert tag_skills("We use Hotwire daily", ["Hotwire"]) == ["Hotwire"]
    assert "GCP" not in tag_skills("Clinical Trial Manager: knowledge of ICH-GCP required")
    assert "GCP" in tag_skills("Data platform on Google Cloud (BigQuery)")


# Filter ------------------------------------------------------------------------
def test_filter_reasons():
    base = recruitee.parse_offer(offer(1, "Data Engineer"), "t")
    assert scout.filter_reason(base, []) is None
    assert "older" in scout.filter_reason(recruitee.parse_offer(offer(2, "x", days_ago=31), "t"), [])
    assert "not in the Netherlands" in scout.filter_reason(recruitee.parse_offer(offer(3, "x", country="DE"), "t"), [])
    assert scout.filter_reason(recruitee.parse_offer(offer(4, "x", country="DE", remote=True), "t"), []) is None
    assert "exclusion" in scout.filter_reason(base, ["data pipelines"])


# Full run ----------------------------------------------------------------------
def test_run_scout_end_to_end(store, tmp_path):
    search = FakeSearch()
    result = scout.run_scout(store, settings(tmp_path), ["data engineer", "dbt"], search=search,
                             client=board_client(), sleep=lambda _s: None)
    assert [q[0] for q in search.queries] == ['site:recruitee.com "data engineer" Amsterdam',
                                              'site:recruitee.com "dbt" Amsterdam']
    assert result["companies_found"] == 3 and result["boards_read"] == 2
    assert result["boards_failed"][0]["slug"] == "brokenboard"
    assert result["postings_discovered"] == 8
    assert result["new_leads"] == 4  # 1, 4, 5, 10
    assert result["filtered_out"] == {"older than 30 days": 1, "not in the Netherlands and not remote": 1,
                                      "matches your exclusion": 1, "no matching skills": 1}

    leads = {l["source_posting_id"]: l for l in store.leads("filtered")["leads"]}
    assert set(leads) == {"recruitee:1", "recruitee:4", "recruitee:5", "recruitee:10"}
    assert leads["recruitee:5"]["language"] == "nl" and "Power BI" in leads["recruitee:5"]["skills"]
    assert "dbt" in leads["recruitee:1"]["skills"] and leads["recruitee:4"]["remote"] is True

    again = scout.run_scout(store, settings(tmp_path), ["data engineer"], search=search,
                            client=board_client(), sleep=lambda _s: None)
    assert again["new_leads"] == 0 and again["duplicates"] == 4  # dedupe on source posting id

    rows = companies.companies(store.conn, "recruitee")
    assert {r["slug"] for r in rows} == {"tulipdata", "grachtsoft", "brokenboard"}
    assert next(r for r in rows if r["slug"] == "brokenboard")["last_error"].endswith("404")
    events = (store.data_dir / "events.jsonl").read_text()
    assert '"subject": "scout"' in events or '"subject":"scout"' in events


def test_run_scout_is_polite(store, tmp_path):
    gaps = []
    scout.run_scout(store, settings(tmp_path), ["x"], search=FakeSearch(), client=board_client(), sleep=gaps.append)
    assert gaps == [scout.REQUEST_GAP_SECONDS] * 2  # 3 boards -> 2 gaps of 1 s


def test_run_scout_needs_terms(tmp_path):
    s = RealStore(tmp_path / "data", REPO_ROOT / "data")
    with pytest.raises(ValueError, match="discovery terms"):
        scout.run_scout(s, settings(tmp_path), None, search=FakeSearch(), client=board_client())


def test_snapshot_builds_panel_and_is_idempotent_per_day(store, tmp_path):
    kw = dict(search=FakeSearch(), client=board_client(), sleep=lambda _s: None)
    first = scout.run_snapshot(store, settings(tmp_path), **kw)
    assert first["panel_size"] == 3 and first["postings"] == 8
    second = scout.run_snapshot(store, settings(tmp_path), **kw)
    assert second["postings"] == 8
    n = store.conn.execute("SELECT COUNT(*) FROM posting_snapshots").fetchone()[0]
    assert n == 8  # same day replaces, not duplicates


# Migration and routes ----------------------------------------------------------
def test_migration_adds_columns_to_an_old_database(tmp_path):
    conn = sqlite3.connect(tmp_path / "app.db")
    conn.execute("CREATE TABLE leads (id TEXT PRIMARY KEY, nickname TEXT UNIQUE, title TEXT, company TEXT, "
                 "source TEXT, url TEXT, fetched INTEGER NOT NULL DEFAULT 0, rate_text TEXT, score INTEGER NULL, "
                 "why TEXT, status TEXT NOT NULL DEFAULT 'new', found_at TEXT, dedup_key TEXT UNIQUE, verdict_note TEXT)")
    conn.execute("INSERT INTO leads (id, nickname, title) VALUES ('ld_old', 'old', 'Old lead')")
    conn.commit()
    conn.close()
    conn = db.connect(tmp_path)
    db.init_schema(conn)
    db.init_schema(conn)  # safe to run twice
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
    assert {"source_posting_id", "language", "skills_json", "apply_url"} <= cols
    assert conn.execute("SELECT title FROM leads WHERE id = 'ld_old'").fetchone()[0] == "Old lead"


def test_scout_route_reports_missing_oxylabs_key(tmp_path):
    app = create_app(settings(tmp_path, key=""))
    with TestClient(app) as c:
        app.state.store.update_persona({"skills": ["Python"]})
        assert c.post("/api/scout", json={"terms": ["python"]}).status_code == 202
        job = c.get("/api/scout/status").json()
        assert job["status"] == "error" and "OXYLABS_WEB_API_KEY" in job["error"]


def test_scout_route_blocked_in_mock_mode(tmp_path):
    s = Settings(mock_mode=True, data_dir=tmp_path / "d", fixtures_dir=REPO_ROOT / "fixtures", mcp_token="t",
                 cors_origins=(), mock_stream_delay=0, web_dist=tmp_path / "x", landing_file=tmp_path / "y.html")
    with TestClient(create_app(s)) as c:
        assert c.post("/api/scout").status_code == 409
