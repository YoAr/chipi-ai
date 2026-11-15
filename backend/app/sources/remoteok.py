from __future__ import annotations

import httpx

from .normalize import matches_keywords, new_lead, strip_html

URL = "https://remoteok.com/api"
HEADERS = {"User-Agent": "Chipi/0.1 (job lead finder)"}


def _rate(item: dict) -> str | None:
    lo, hi = item.get("salary_min"), item.get("salary_max")
    if lo and hi:
        return f"${int(lo):,}-${int(hi):,}/year"
    return None


def parse(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("position"):
            continue  # metadata element or junk
        desc = strip_html(item.get("description") or "")
        out.append(
            new_lead(
                title=item.get("position", ""),
                company=item.get("company", ""),
                source="remoteok",
                url=item.get("url") or item.get("apply_url") or "",
                fetched=bool(desc),
                rate_text=_rate(item),
                description=desc,
                tags=item.get("tags") or [],
            )
        )
    return out


def fetch(client: httpx.Client, keywords: list[str], limit: int = 30) -> list[dict]:
    try:
        r = client.get(URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        leads = parse(r.json())
    except (httpx.HTTPError, ValueError):
        return []
    leads = [
        l
        for l in leads
        if matches_keywords(
            f"{l['title']} {l['company']} {' '.join(l.get('tags', []))} {l['description']}",
            keywords,
        )
    ]
    return leads[:limit]
