import json
from pathlib import Path

from app.sources import hn, remoteok, remotive
from app.sources.normalize import dedup_key, make_nickname, new_lead

DATA = Path(__file__).parent / "data"
LEAD_FIELDS = {"id", "nickname", "title", "company", "source", "url", "fetched",
               "rate_text", "score", "why", "status", "found_at", "dedup_key"}


def load(name):
    return json.loads((DATA / name).read_text())


def check_leads(leads, source):
    assert leads
    for l in leads:
        assert LEAD_FIELDS <= set(l)
        assert l["source"] == source
        assert l["id"].startswith("ld_")
        assert l["score"] is None and l["status"] == "new" and l["why"] == ""
        assert l["found_at"].endswith("Z")
        assert l["title"] and l["url"]
        assert len(l.get("description", "")) <= 4000
        assert "<" not in l.get("description", "")


def test_parse_remoteok_skips_metadata():
    raw = load("remoteok_sample.json")
    leads = remoteok.parse(raw)
    check_leads(leads, "remoteok")
    assert len(leads) <= len(raw) - 1 or "position" in raw[0]


def test_parse_remotive():
    check_leads(remotive.parse(load("remotive_sample.json")), "remotive")


def test_parse_hn():
    leads = hn.parse(load("hn_sample.json"))
    check_leads(leads, "hn_whos_hiring")
    assert all(l["fetched"] for l in leads)


def test_parse_garbage():
    for m in (remoteok, remotive, hn):
        assert m.parse(None) == []
        assert m.parse({}) == [] or m is remoteok
        assert m.parse([]) == []


def test_hn_rate_text():
    raw = {"hits": [{"objectID": "1", "comment_text": "Acme | Python | $120-150/hour<p>Remote"},
                    {"objectID": "2", "comment_text": "Foo | Go<p>No pay info"}]}
    a, b = hn.parse(raw)
    assert "$120" in a["rate_text"]
    assert b["rate_text"] is None
    assert a["company"] == "Acme"


def test_dedup_key():
    a = dedup_key("Acme, Inc.", "Senior Python Dev", "https://x.com/jobs/1/?utm=a#top")
    b = dedup_key("acme inc", "senior  python dev!", "https://x.com/jobs/1")
    assert a == b
    assert a != dedup_key("Acme", "Senior Python Dev", "https://x.com/jobs/2")


def test_nickname():
    assert make_nickname("Senior Backend Engineer (Python) at Acme", set()) == "senior-backend-engineer"
    taken = {"backend-engineer"}
    assert make_nickname("Backend Engineer", taken) == "backend-engineer-2"
    taken.add("backend-engineer-2")
    assert make_nickname("Backend Engineer", taken) == "backend-engineer-3"
    assert make_nickname("!!!", set()) == "lead"


def test_new_lead_shape():
    l = new_lead(title="T", company="C", source="remoteok", url="https://a.com/x/")
    assert LEAD_FIELDS <= set(l)
    assert l["dedup_key"] == dedup_key("C", "T", "https://a.com/x")
    assert new_lead()["id"] != new_lead()["id"]
