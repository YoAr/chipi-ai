"""Shared test setup: no test may reach OpenAI."""

import pytest


@pytest.fixture(autouse=True)
def _no_openai_at_startup(monkeypatch):
    # App startup lists the models for the key when one is set; never let a test make that call.
    monkeypatch.setattr("app.openai_check._list_model_ids", lambda: [])


def minimal_profile(**over) -> dict:
    """A spec v2 §5.1 Profile with nothing in it but a rate floor (EUR/hour)."""
    return {"skills": [], "skill_combinations": [], "industries": [], "preferred_work": [], "exclusions": [],
            "rate_floor_eur_hour": 100, "availability_hours_week": None, "past_employers": [], "schools": [],
            "languages": ["en"], "voice_samples": [], **over}


@pytest.fixture()
def approve_profile():
    """approve_profile(client, floor=100, **profile_fields): save and approve a profile through the API,
    so the rate floor comes from where the app reads it (app.profiles.rate_floor)."""
    def approve(client, floor: float = 100, **over) -> dict:
        saved = client.put("/api/profile", json=minimal_profile(rate_floor_eur_hour=floor, **over))
        assert saved.status_code == 200, saved.text
        res = client.post("/api/profile/approve", json={"version": saved.json()["version"]})
        assert res.status_code == 200, res.text
        return res.json()
    return approve
