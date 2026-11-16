"""Researcher (spec v2 §5.3) and the page fetcher. Fake search, pages and model; no network."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import profiles, service_catalog
from app.ai import researcher
from app.ai.researcher import Brief, Hook, Source, warm_connection
from app.config import REPO_ROOT, Settings
from app.guards import RateLimiter
from app.main import create_app
from app.providers.fetcher import PageFetcher, is_public_url, published_date
from app.storage import versions

NOW = datetime.now(timezone.utc)
D = lambda n: (NOW - timedelta(days=n)).strftime("%Y-%m-%d")  # noqa: E731


# Fetcher ---------------------------------------------------------------------------
@pytest.mark.parametrize("html, url, want", [
    ('<meta property="article:published_time" content="2026-11-02T08:00:00+01:00">', "", "2026-11-02"),
    ('<meta content="2026-10-30" name="date">', "", "2026-10-30"),
    ('<script type="application/ld+json">{"@graph":[{"datePublished":"2026-11-05"}]}</script>', "", "2026-11-05"),
    ('<time datetime="2026-11-07T10:00">7 nov</time>', "", "2026-11-07"),
    ("<p>no date</p>", "https://x.nl/nieuws/2026/11/09/launch", "2026-11-09"),
    ("<p>no date</p>", "https://x.nl/about", None),
])
def test_published_date_comes_from_the_page(html, url, want):
    assert published_date(html, url) == want


def test_internal_addresses_are_refused():
    for url in ("http://localhost:8010/api/state", "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data",
                "http://10.0.0.5/", "file:///etc/passwd", "ftp://example.com/x"):
        assert not is_public_url(url), url


def test_fetcher_refuses_a_redirect_to_an_internal_address():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
    f = PageFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)),
                    url_check=lambda u: "127.0.0.1" not in u)
    page = f.fetch("https://news.example.nl/a")
    assert page["error"] == "redirected to a non-public address" and page["text"] == ""


def test_fetcher_falls_back_to_oxylabs_when_blocked():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "webapi.oxylabs.io":
            assert request.headers["authorization"] == "Bearer K"
            assert json.loads(request.content) == {"url": "https://news.example.nl/a", "output": ["html"]}
            return httpx.Response(200, json={"status": 200, "results": [{
                "html": '<title>Tulip raises</title><meta name="date" content="2026-11-10"><p>Tulip Data raised 5 million</p>',
                "metadata": {"url": "https://news.example.nl/a"}}]})
        return httpx.Response(403)

    f = PageFetcher("K", httpx.Client(transport=httpx.MockTransport(handler)), url_check=lambda u: True)
    page = f.fetch("https://news.example.nl/a")
    assert page["via"] == "oxylabs" and page["published_at"] == "2026-11-10" and "5 million" in page["text"]
    assert f.scrape_calls == 1


# Researcher ------------------------------------------------------------------------
PROFILE = {
    "skills": [{"name": "Python", "taxonomy_id": None, "proof_strength": "referenceable",
                "evidence": [{"claim": "Pipelines", "metric": None, "client": "Grachtbank", "citable": True}]},
               {"name": "dbt", "taxonomy_id": None, "proof_strength": "demonstrated",
                "evidence": [{"claim": "Models", "metric": None, "client": None, "citable": False}]}],
    "skill_combinations": [], "industries": ["fintech"], "preferred_work": ["data platform rebuilds"],
    "exclusions": [], "rate_floor_eur_hour": 95, "availability_hours_week": 24,
    "past_employers": ["Tulip Data B.V."], "schools": ["Universiteit van Amsterdam"], "languages": ["nl", "en"],
    "voice_samples": [],
}
SERVICES = [{"id": "dbt-rebuild", "name": "dbt model rebuild", "target_buyer": "Scale-ups", "problem": "Slow loads",
             "deliverable": "Migrated dbt project", "duration_weeks": 6, "skills_used": ["Python", "dbt"],
             "rate_range_eur": {"min": 110, "max": 130}, "demand_index": 0.5, "proof_strength": "referenceable",
             "independence_rationale": "Fixed scope", "rationale": "x",
             "discovery_terms": {"nl": ["data engineer", "dbt"], "en": ["data engineer", "analytics engineer"]}}]

LEAD = {"title": "Senior Data Engineer", "company": "Kanaal Analytics", "source": "recruitee",
        "url": "https://kanaal.recruitee.com/o/sde", "fetched": True, "status": "filtered",
        "dedup_key": "recruitee:77", "source_posting_id": "recruitee:77", "company_slug": "kanaal",
        "department": "Data", "location": "Amsterdam", "remote": False, "employment_type": "fulltime_permanent",
        "language": "nl", "published_at": (NOW - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "description": "Wij bouwen ons dataplatform opnieuw met dbt en Python.", "apply_url": "https://kanaal.recruitee.com/o/sde/c/new",
        "skills": ["Python", "dbt"]}

PAGES = {
    "https://news.example.nl/kanaal-funding": {"title": "Kanaal Analytics haalt 8 miljoen op", "published_at": D(6),
                                               "text": "Kanaal Analytics heeft 8 miljoen euro opgehaald om het dataplatform uit te breiden."},
    "https://blog.example.nl/old": {"title": "Kanaal Analytics opent kantoor", "published_at": D(90),
                                    "text": "Kanaal Analytics opende een kantoor."},
    "https://other.example.nl/x": {"title": "Something else", "published_at": D(2), "text": "Nothing about them"},
}


class FakeSearch:
    calls = 0

    def search(self, query, count=10, locale="NL"):
        self.calls += 1
        return [{"url": u, "title": p["title"], "snippet": ""} for u, p in PAGES.items()] + [
            {"url": "https://www.linkedin.com/in/someone", "title": "Kanaal Analytics CTO", "snippet": ""},
            {"url": "https://nl.indeed.com/kanaal", "title": "Kanaal Analytics vacatures", "snippet": ""}]


class FakeFetcher:
    scrape_calls = 0

    def __init__(self):
        self.fetched = []

    def fetch(self, url):
        self.fetched.append(url)
        p = PAGES[url]
        return {"url": url, "status": 200, "via": "http", "title": p["title"], "text": p["text"],
                "published_at": p["published_at"]}


def brief(fit=True, url="https://news.example.nl/kanaal-funding", summary="Kanaal Analytics haalde 8 miljoen euro op",
          date="2020-01-01", service="dbt-rebuild"):
    return Brief(fit=fit, fit_reason="dbt rebuild matches" if fit else "They need a permanent hire for SAP, no service fits",
                 service_id=service if fit else None,
                 hook=Hook(summary=summary, source_url=url, published_at=date, type="funding") if fit else None,
                 problem_hypothesis="They likely need the platform rebuilt fast after the round." if fit else "",
                 channel="direct_note", warm_connection=None, draft_language="en",
                 sources=[Source(url=url, title="t", published_at=date)] if fit else [])


class FakeModel:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, role, settings, *, instructions, input, schema, tools=None):
        self.calls.append({"role": role, "input": input})
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def settings(tmp_path):
    return Settings(mock_mode=False, data_dir=tmp_path / "data", fixtures_dir=REPO_ROOT / "fixtures", mcp_token="t",
                    cors_origins=(), mock_stream_delay=0, web_dist=tmp_path / "x", landing_file=tmp_path / "y.html",
                    openai_key_set=True, model_smart="s", model_check="check-model", model_fast="f", oxylabs_key="k")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    app = create_app(settings(tmp_path))
    app.state.action_limiter = RateLimiter(max_calls=1000, per_seconds=60)
    with TestClient(app) as c:
        store = app.state.store
        v = profiles.edit(store, PROFILE)
        profiles.approve(store, v["version"])
        sv = versions.save(store.conn, "services", {"services": SERVICES, "market": None}, author="agent")
        service_catalog.approve(store, sv["version"])
        lead = store.upsert_leads([dict(LEAD)])[0]
        yield c, store, lead


def run(store, tmp_path, lead, model, monkeypatch):
    monkeypatch.setattr(researcher.ai, "structured", model)
    return researcher.research_lead(store, settings(tmp_path), store.lead(lead["id"]),
                                    search=FakeSearch(), fetcher=FakeFetcher())


def test_sources_skip_people_job_boards_stale_and_unrelated_pages():
    sources, cost = researcher.gather_sources(dict(LEAD), FakeSearch(), FakeFetcher(), NOW)
    assert [s["url"] for s in sources] == ["https://news.example.nl/kanaal-funding", LEAD["url"]]
    assert sources[-1]["id"] == "P" and sources[-1]["published_at"] == LEAD["published_at"][:10]
    assert cost["searches"] == 2


def test_fit_brief_uses_page_date_code_channel_and_saves_the_source(env, tmp_path, monkeypatch):
    c, store, lead = env
    model = FakeModel([brief()])
    res = run(store, tmp_path, lead, model, monkeypatch)
    assert res["status"] == "researched" and model.calls[0]["role"] == "check"
    b = store.lead(lead["id"])["brief"]
    assert b["hook"]["published_at"] == D(6)          # from the page, not the model's 2020-01-01
    assert b["channel"] == "apply_interim"            # no overlap, no role address: code decides
    assert b["draft_language"] == "nl"                # follows the posting
    assert b["hook"]["raw"].startswith("raw/") and (store.data_dir / b["hook"]["raw"]).read_text().startswith("URL: https://news.example.nl")
    assert [s["url"] for s in b["sources"]] == ["https://news.example.nl/kanaal-funding"]
    assert b["provenance"]["model"] == "check-model" and b["provenance"]["prompt_version"] == researcher.PROMPT_VERSION
    assert store.lead(lead["id"])["status"] == "researched"
    assert '"lead_researched"' in (store.data_dir / "events.jsonl").read_text()


def test_invented_hook_is_retried_then_left_for_later(env, tmp_path, monkeypatch):
    c, store, lead = env
    bad = brief(url="https://made-up.example.com/news")
    model = FakeModel([bad, bad])
    res = run(store, tmp_path, lead, model, monkeypatch)
    assert res["status"] == "failed" and len(model.calls) == 2
    assert "copied exactly from one candidate source" in model.calls[1]["input"]
    after = store.lead(lead["id"])
    assert after["status"] == "filtered" and "brief" not in after and after["research_error"]


def test_numbers_must_be_on_the_source_page(env, tmp_path, monkeypatch):
    c, store, lead = env
    model = FakeModel([brief(summary="Kanaal Analytics raised 12 million"), brief()])
    res = run(store, tmp_path, lead, model, monkeypatch)
    assert res["status"] == "researched"
    assert "numbers not on the source page: 12" in model.calls[1]["input"]


def test_stale_or_unknown_service_is_refused(env, tmp_path, monkeypatch):
    c, store, lead = env
    model = FakeModel([brief(service="sap-migration"), brief(service="sap-migration")])
    assert run(store, tmp_path, lead, model, monkeypatch)["status"] == "failed"
    assert "service_id must be one of: dbt-rebuild" in model.calls[1]["input"]


def test_posting_is_a_valid_fallback_hook(env, tmp_path, monkeypatch):
    c, store, lead = env
    model = FakeModel([brief(url=LEAD["url"], summary="Kanaal Analytics zoekt een Senior Data Engineer")])
    run(store, tmp_path, lead, model, monkeypatch)
    hook = store.lead(lead["id"])["brief"]["hook"]
    assert hook["type"] == "posting" and hook["published_at"] == LEAD["published_at"][:10] and "raw" not in hook


def test_no_fit_drops_the_lead_with_a_reason(env, tmp_path, monkeypatch):
    c, store, lead = env
    run(store, tmp_path, lead, FakeModel([brief(fit=False)]), monkeypatch)
    after = store.lead(lead["id"])
    assert after["status"] == "dropped" and after["brief"]["hook"] is None and "SAP" in after["why"]
    assert '"lead_dropped"' in (store.data_dir / "events.jsonl").read_text()


def test_warm_intro_when_the_company_is_a_past_employer():
    assert warm_connection("Tulip Data", PROFILE) == "Tulip Data B.V."
    assert warm_connection("Grachtbank N.V.", PROFILE) == "Grachtbank"
    assert warm_connection("Kanaal Analytics", PROFILE) is None
    assert warm_connection("BV", PROFILE) is None


def test_research_route_needs_approved_services(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as c:
        assert c.post("/api/research", json={"limit": 5}).status_code == 202
        job = c.get("/api/research/status").json()
        assert job["status"] == "error" and "Approve your profile and services" in job["error"]


def test_batch_continues_past_a_failing_lead(env, tmp_path, monkeypatch):
    c, store, lead = env
    second = store.upsert_leads([{**LEAD, "dedup_key": "recruitee:78", "source_posting_id": "recruitee:78",
                                  "url": "https://kanaal.recruitee.com/o/ml"}])[0]
    model = FakeModel([brief(url="https://nope.example.com"), brief(url="https://nope.example.com"), brief()])
    monkeypatch.setattr(researcher.ai, "structured", model)
    out = researcher.run_research(store, settings(tmp_path), limit=10, search=FakeSearch(), fetcher=FakeFetcher())
    assert out["leads"] == 2 and out["failed"] == 1 and out["researched"] == 1


def test_relevance_ranks_leads_for_research(env):
    from app.relevance import score_lead
    c, store, lead = env
    other = store.upsert_leads([{**LEAD, "title": "Voorman GWW", "skills": ["Process improvement"],
                                 "dedup_key": "recruitee:90", "source_posting_id": "recruitee:90",
                                 "url": "https://kanaal.recruitee.com/o/voorman",
                                 "published_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}])[0]
    assert score_lead(store.lead(lead["id"]), SERVICES) == (100, "dbt-rebuild")  # both tags + "data engineer" in title
    assert score_lead(store.lead(other["id"]), SERVICES) == (0, None)
    picked = store.leads_to_research(1, SERVICES)
    assert [l["id"] for l in picked] == [lead["id"]]          # relevance beats the newer posting
    assert store.lead(other["id"])["score"] == 0               # scores are saved for the UI


@pytest.mark.parametrize("url, skip", [
    ("https://www.infrasite.nl/tag/kws-infra/", True),
    ("https://nl.tonyschocolonely.com/en/blogs/news/", True),
    ("https://www.nlr.org/nl/newsroom/", True),
    ("https://example.nl/", True),
    ("https://careers.kanaal.nl/o/sde", True),          # the posting's own site
    ("https://www.linkedin.com/in/someone", True),
    ("https://cloudexpo.nl/nieuws/optics11-haalt-50-miljoen-euro-op", False),
    ("https://www.nlr.org/nl/newsroom/nieuws/to2-impactrapportage-2026", False),
])
def test_listing_pages_people_and_own_site_are_not_sources(url, skip):
    assert researcher._skip(url, {"careers.kanaal.nl"}) is skip
