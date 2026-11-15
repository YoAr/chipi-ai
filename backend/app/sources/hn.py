from __future__ import annotations

import re

import httpx

from .normalize import matches_keywords, new_lead, strip_html

STORIES_URL = "https://hn.algolia.com/api/v1/search_by_date"
COMMENTS_URL = "https://hn.algolia.com/api/v1/search"
ITEM_URL = "https://news.ycombinator.com/item?id="
_RATE_RE = re.compile(
    r"\$\s?\d[\d,.]*\s?[kK]?(?:\s?(?:-|–|to)\s?\$?\s?\d[\d,.]*\s?[kK]?)?(?:\s?(?:/|per)\s?(?:hr|hour|yr|year|month))?"
)


def _rate(text: str) -> str | None:
    m = _RATE_RE.search(text)
    return m.group(0).strip() if m else None


def parse(raw) -> list[dict]:
    hits = raw.get("hits") if isinstance(raw, dict) else None
    if not isinstance(hits, list):
        return []
    out = []
    for h in hits:
        if not isinstance(h, dict) or not h.get("comment_text"):
            continue
        text = strip_html(h["comment_text"])
        if not text:
            continue
        first = text.split("\n", 1)[0]
        company = first.split("|", 1)[0].strip()[:80]
        title = first[:140]
        out.append(
            new_lead(
                title=title,
                company=company,
                source="hn_whos_hiring",
                url=f"{ITEM_URL}{h.get('objectID', '')}",
                fetched=True,
                rate_text=_rate(text),
                description=text,
            )
        )
    return out


def fetch(client: httpx.Client, keywords: list[str], limit: int = 30) -> list[dict]:
    try:
        r = client.get(
            STORIES_URL,
            params={"tags": "story,author_whoishiring", "hitsPerPage": 5},
            timeout=15,
        )
        r.raise_for_status()
        story_id = None
        for s in r.json().get("hits", []):
            if "who is hiring" in (s.get("title") or "").lower():
                story_id = s.get("objectID")
                break
        if not story_id:
            return []
        r = client.get(
            COMMENTS_URL,
            params={
                "tags": f"comment,story_{story_id}",
                "hitsPerPage": 1000,
                "query": " ".join(keywords),
            },
            timeout=15,
        )
        r.raise_for_status()
        leads = parse(r.json())
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    leads = [l for l in leads if matches_keywords(l["description"], keywords)]
    return leads[:limit]
