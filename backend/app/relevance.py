"""Lead relevance to the approved services, in code (no model call), so the Researcher spends its
searches and model calls on the leads most likely to fit.

    score (0-100) = max over services of  60 x tag overlap  +  40 x title hit
    tag overlap   = |posting skill tags ∩ the service's skill tags| / min(3, |service tags|)
    title hit     = the job title contains one of the service's NL or EN discovery terms

Only a ranking aid: the Researcher still decides fit and can drop any lead.
"""

from __future__ import annotations

from .tagging import tag_skills


def service_tags(service: dict) -> set[str]:
    return {t for skill in service.get("skills_used", []) for t in tag_skills(skill)}


def score_lead(lead: dict, services: list[dict]) -> tuple[int, str | None]:
    """(score 0-100, id of the best-matching service or None)."""
    skills = set(lead.get("skills") or [])
    title = (lead.get("title") or "").lower()
    best, best_id = 0, None
    for s in services:
        tags = service_tags(s)
        overlap = len(skills & tags) / min(3, len(tags)) if tags else 0.0
        terms = [t.lower() for lang in ("nl", "en") for t in s.get("discovery_terms", {}).get(lang, []) if t.strip()]
        title_hit = any(t in title for t in terms)
        score = round(60 * min(1.0, overlap) + 40 * title_hit)
        if score > best:
            best, best_id = score, s["id"]
    return best, best_id


def rank(leads: list[dict], services: list[dict]) -> list[tuple[dict, int, str | None]]:
    """Leads with their scores, best first; newer postings break ties."""
    scored = [(lead, *score_lead(lead, services)) for lead in leads]
    scored.sort(key=lambda x: (x[1], x[0].get("published_at") or ""), reverse=True)
    return scored
