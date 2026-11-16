"""Shared test setup: no test may reach OpenAI."""

import pytest


@pytest.fixture(autouse=True)
def _no_openai_at_startup(monkeypatch):
    # App startup lists the models for the key when one is set; never let a test make that call.
    monkeypatch.setattr("app.openai_check._list_model_ids", lambda: [])
