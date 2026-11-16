"""SearchProvider (spec v2 §7) with the Oxylabs Web API as the implementation.

    POST https://webapi.oxylabs.io/v1/search
    Authorization: Bearer $OXYLABS_WEB_API_KEY
    {"query": "...", "max_results": 1-20, "location": "NL"}
    -> {"state": "done", "results": [{"title", "outline": [str], "url", "metadata": {"position"}}]}

Verified against the live API 2026-11-15. The endpoint has no recency filter and returns
no publish dates, so `published_at` is always None here: callers that need "last 30 days"
check the date on the fetched page instead (spec: hook dated within 30 days, in code).
Unknown request parameters are rejected with 400, so only these three are ever sent.
"""

from __future__ import annotations

import time
from typing import Protocol

import httpx

from ..ai.client import AIUnavailable

URL = "https://webapi.oxylabs.io/v1/search"
MAX_RESULTS = 20


class ProviderUnavailable(AIUnavailable):
    """An outside service (search, job board) can't be used right now. Jobs show the message."""


class SearchProvider(Protocol):
    def search(self, query: str, count: int = 10, locale: str | None = "NL") -> list[dict]:
        """[{url, title, snippet, published_at}] best first."""
        ...


class OxylabsSearch:
    def __init__(self, api_key: str, client: httpx.Client | None = None, retries: int = 2,
                 sleep=time.sleep) -> None:
        if not api_key:
            raise ProviderUnavailable("Oxylabs is not configured: set OXYLABS_WEB_API_KEY")
        self._key = api_key
        self._client = client or httpx.Client(timeout=120)
        self._retries = retries
        self._sleep = sleep
        self.calls = 0  # for cost reporting (spec §10: cost per ready draft)

    def search(self, query: str, count: int = 10, locale: str | None = "NL") -> list[dict]:
        query = (query or "").strip()
        if not 1 <= len(query) <= 2048:
            raise ValueError("Search query must be 1-2048 characters")
        body: dict = {"query": query, "max_results": max(1, min(int(count), MAX_RESULTS))}
        if locale:
            body["location"] = locale.upper()
        headers = {"Authorization": f"Bearer {self._key}"}
        for attempt in range(self._retries + 1):
            self.calls += 1
            try:
                r = self._client.post(URL, json=body, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == self._retries:
                    raise ProviderUnavailable(f"Oxylabs search failed: {type(exc).__name__}") from exc
                self._sleep(2 ** attempt)
                continue
            if r.status_code == 401:
                raise ProviderUnavailable("Oxylabs rejected the API key (401). Check OXYLABS_WEB_API_KEY")
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == self._retries:
                    raise ProviderUnavailable(f"Oxylabs search failed: HTTP {r.status_code}")
                self._sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise ProviderUnavailable(f"Oxylabs refused the search: HTTP {r.status_code} {r.text[:200]}")
            data = r.json()
            return [
                {"url": item.get("url") or "", "title": item.get("title") or "",
                 "snippet": " ".join(item.get("outline") or [])[:1000], "published_at": None}
                for item in data.get("results") or [] if item.get("url")
            ]
        return []  # unreachable


def search_provider(settings, client: httpx.Client | None = None) -> SearchProvider:
    return OxylabsSearch(settings.oxylabs_key, client)
