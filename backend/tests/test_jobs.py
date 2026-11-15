from app import jobs
from app.storage import RealStore
from app.config import REPO_ROOT


def _store(tmp_path):
    return RealStore(tmp_path / "data", REPO_ROOT / "data")


def test_job_lifecycle(tmp_path):
    s = _store(tmp_path)
    assert jobs.get(s, "search")["status"] == "idle"
    assert jobs.begin(s, "search", step="fetching") is True
    assert jobs.begin(s, "search", step="fetching") is False  # already running
    jobs.step(s, "search", "scoring", "Scoring 12 leads")
    assert jobs.get(s, "search")["step"] == "scoring"
    jobs.done(s, "search", "Added 5", {"added": 5})
    job = jobs.get(s, "search")
    assert job["status"] == "done" and job["result"] == {"added": 5} and job["step"] is None
    assert jobs.begin(s, "search", step="fetching") is True  # can run again after done


def test_failed_job_records_error(tmp_path):
    s = _store(tmp_path)
    jobs.begin(s, "profile", step="persona")
    jobs.fail(s, "profile", "model_unavailable: no credits")
    job = jobs.get(s, "profile")
    assert job["status"] == "error" and "no credits" in job["error"]
