from __future__ import annotations

import httpx

from .normalize import matches_keywords, new_lead, strip_html

URL = "https://remotive.com/api/remote-jobs"


def parse(raw) -> list[dict]:
    jobs = raw.get("jobs") if isinstance(raw, dict) else None
    if not isinstance(jobs, list):
        return []
    out = []
    for j in jobs:
        if not isinstance(j, dict) or not j.get("title"):
            continue
        desc = strip_html(j.get("description") or "")
        out.append(
            new_lead(
                title=j.get("title", ""),
                company=j.get("company_name", ""),
                source="remotive",
                url=j.get("url", ""),
                fetched=bool(desc),
                rate_text=(j.get("salary") or "").strip() or None,
                description=desc,
            )
        )
    return out


def fetch(client: httpx.Client, keywords: list[str], limit: int = 30) -> list[dict]:
    try:
        r = client.get(
            URL, params={"search": " ".join(keywords), "limit": limit}, timeout=15
        )
        r.raise_for_status()
        leads = parse(r.json())
    except (httpx.HTTPError, ValueError):
        return []
    leads = [
        l
        for l in leads
        if matches_keywords(f"{l['title']} {l['description']}", keywords)
    ]
    return leads[:limit]
