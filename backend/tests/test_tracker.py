"""Tracker (spec v2 §6.3): statuses, reminders, one follow-up, company stop, funnel. Fake models; no network."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import tracker
from app.ai import drafts
from app.ai.drafts import WriterDraft
from test_drafts_v2 import GOOD_NL, LEAD, Fake, FakeFetcher, env, settings, wd  # noqa: F401  (fixture)

FOLLOW = ("Een korte aanvulling op mijn vorige bericht: ik begin met een herbouw van jullie dbt-modellen, "
          "met tests en overdracht. Bij Grachtbank verkortte ik de nachtelijke laadtijd met 40%. Zal ik een scope sturen?")


def ready_and_copied(store, tmp, lead, monkeypatch):
    monkeypatch.setattr(drafts.ai, "structured", Fake([wd()]))
    d = drafts.write_and_verify(store, settings(tmp), lead, fetcher=FakeFetcher())
    store.copy_draft(d["id"], d["body"])
    return d


def test_business_days_skip_weekends():
    friday = datetime(2026, 11, 13, 10, tzinfo=timezone.utc)
    assert tracker.add_business_days(friday, 5) == datetime(2026, 11, 20, 10, tzinfo=timezone.utc)
    assert tracker.add_business_days(friday, 1).weekday() == 0  # Monday


def test_sent_sets_a_reminder_and_outcome_closes_it(env, monkeypatch):
    c, store, lead, tmp = env
    ready_and_copied(store, tmp, lead, monkeypatch)
    out = c.post(f"/api/leads/{lead['id']}/status", json={"status": "sent"}).json()
    assert out["status"] == "sent"
    assert c.get("/api/reminders").json()["reminders"] == []                 # not due yet
    upcoming = c.get("/api/reminders?all=true").json()["reminders"]
    assert len(upcoming) == 1 and upcoming[0]["what"] == "Did they reply?" and upcoming[0]["followup_allowed"]
    later = datetime.now(timezone.utc) + timedelta(days=8)
    due = tracker.reminders(store, now=later)
    assert len(due) == 1 and due[0]["lead"]["id"] == lead["id"]

    c.post(f"/api/leads/{lead['id']}/status", json={"status": "positive_reply", "note": "Call on Thursday"})
    assert tracker.reminders(store, now=later) == []
    ev = [json.loads(l) for l in (store.data_dir / "events.jsonl").read_text().splitlines()]
    changes = [e for e in ev if e["type"] == "lead_status_changed"]
    assert [(e["payload"]["from"], e["payload"]["to"]) for e in changes] == [("copied", "sent"), ("sent", "positive_reply")]
    p = changes[-1]["payload"]
    assert p["service_id"] == "dbt-rebuild" and p["channel"] == "apply_interim" and p["hook_type"] == "funding"
    assert p["language"] == "nl" and p["note"] == "Call on Thursday"


def test_invalid_moves_are_refused(env):
    c, store, lead, tmp = env
    res = c.post(f"/api/leads/{lead['id']}/status", json={"status": "won"})
    assert res.status_code == 409 and "Can't move a lead from researched to won" in res.json()["error"]["message"]
    assert c.post(f"/api/leads/{lead['id']}/status", json={"status": "teleported"}).status_code == 422


def test_one_follow_up_built_on_the_sent_message(env, monkeypatch):
    c, store, lead, tmp = env
    first = ready_and_copied(store, tmp, lead, monkeypatch)
    edited = first["body"].replace("Zal ik", "Zullen we")
    store.copy_draft(first["id"], edited)
    assert c.post(f"/api/leads/{lead['id']}/followup", json={}).status_code == 409   # not sent yet
    tracker.set_status(store, lead["id"], "sent")
    tracker.set_status(store, lead["id"], "no_reply")

    fake = Fake([wd(FOLLOW)])
    monkeypatch.setattr(drafts.ai, "structured", fake)
    monkeypatch.setattr(drafts, "PageFetcher", lambda key: FakeFetcher())
    assert c.post(f"/api/leads/{lead['id']}/followup", json={}).status_code == 202
    job = c.get(f"/api/leads/{lead['id']}/draft/status").json()
    assert job["status"] == "done" and job["result"]["status"] == "ready"
    assert "previous_message" in fake.calls[0]["instructions"] or "PREVIOUS MESSAGE" in fake.calls[0]["instructions"]
    follow = next(d for d in store.drafts()["drafts"] if d.get("kind") == "followup")
    names = [x["name"] for x in follow["verifier"]["checks"]]
    assert "Hook facts are on the source page" not in names and "At most 120 words" in names
    assert store.lead(lead["id"])["status"] == "no_reply"                     # a follow-up doesn't move the lead back

    store.copy_draft(follow["id"], follow["body"])
    assert not tracker.followup_allowed(store, store.lead(lead["id"]))
    assert c.post(f"/api/leads/{lead['id']}/followup", json={}).status_code == 409   # max one


def test_follow_up_input_contains_the_text_actually_sent(env, monkeypatch):
    c, store, lead, tmp = env
    first = ready_and_copied(store, tmp, lead, monkeypatch)
    store.copy_draft(first["id"], "MIJN EIGEN VERSIE " + first["body"])
    tracker.set_status(store, lead["id"], "sent")
    seen = {}

    def model(role, s, *, instructions, input, schema, tools=None):
        seen.setdefault(role, input)
        return wd(FOLLOW) if schema is WriterDraft else drafts.Review(passed=True, issues=[], revision_notes=None)

    monkeypatch.setattr(drafts.ai, "structured", model)
    drafts.write_and_verify(store, settings(tmp), store.lead(lead["id"]), kind="followup", fetcher=FakeFetcher())
    assert "MIJN EIGEN VERSIE" in json.loads(seen["smart"])["previous_message"]


def test_not_interested_stops_the_company(env, monkeypatch):
    c, store, lead, tmp = env
    sibling = store.upsert_leads([{**LEAD, "company_slug": None, "dedup_key": "recruitee:78",
                                   "source_posting_id": "recruitee:78", "url": "https://k/ml", "title": "ML Engineer"}])[0]
    store.conn.execute("UPDATE leads SET company_slug = NULL")  # match on company name when no slug
    store.conn.commit()
    ready_and_copied(store, tmp, lead, monkeypatch)
    tracker.set_status(store, lead["id"], "sent")
    out = tracker.set_status(store, lead["id"], "not_interested", "Not now")
    assert out["status"] == "not_interested"
    assert store.lead(sibling["id"])["status"] == "dropped"
    assert "not interested" in store.lead(sibling["id"])["why"]
    with pytest.raises(ValueError, match="not interested"):
        drafts.write_and_verify(store, settings(tmp), store.lead(lead["id"]), fetcher=FakeFetcher())
    assert store.leads_to_research(10, []) == [] and store.leads_to_draft(10) == []
    assert tracker.reminders(store, include_upcoming=True) == []


def test_funnel_counts_and_rates(env, monkeypatch):
    c, store, lead, tmp = env
    ready_and_copied(store, tmp, lead, monkeypatch)
    tracker.set_status(store, lead["id"], "sent")
    tracker.set_status(store, lead["id"], "positive_reply")
    other = store.upsert_leads([{**LEAD, "dedup_key": "recruitee:80", "source_posting_id": "recruitee:80", "url": "https://k/x"}])[0]
    store.conn.execute("UPDATE leads SET status = 'sent' WHERE id = ?", (other["id"],))
    store.conn.commit()
    f = c.get("/api/funnel").json()
    stages = {s["stage"]: s["count"] for s in f["stages"]}
    assert stages["discovered"] == 2 and stages["sent"] == 2 and stages["replied"] == 1 and stages["positive_reply"] == 1
    assert f["rates"]["positive_reply_rate"] == 0.5 and f["rates"]["sent"] == 2
    assert f["rates"]["logging_rate"] == 1.0  # the one copied draft has an outcome logged


def test_drafting_outside_a_job_leaves_no_stuck_job(env, monkeypatch):
    from app import jobs
    c, store, lead, tmp = env
    ready_and_copied(store, tmp, lead, monkeypatch)       # direct call, as the batch does
    assert jobs.get(store, drafts.job_key(lead["id"]))["status"] == "idle"


def test_no_reply_keeps_the_reminder_and_offers_the_follow_up(env, monkeypatch):
    c, store, lead, tmp = env
    ready_and_copied(store, tmp, lead, monkeypatch)
    tracker.set_status(store, lead["id"], "sent", business_days=0)
    tracker.set_status(store, lead["id"], "no_reply")
    due = tracker.reminders(store)
    assert len(due) == 1 and due[0]["lead"]["status"] == "no_reply" and due[0]["followup_allowed"]
    tracker.set_status(store, lead["id"], "sent", business_days=0)          # the follow-up went out
    assert len(tracker.reminders(store, include_upcoming=True)) == 1        # one reminder, replaced not duplicated
