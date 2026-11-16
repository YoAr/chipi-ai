"""Wet DBA framing guidance in code (spec v2 §2.4, §5.2, §5.5).

Since 1 January 2025 the Dutch tax authority enforces against disguised employment. Services
and drafts must read as independent, project-based work with a defined deliverable, never as
an extra team member on set hours under the client's direction.

This is a starter list for a code check, not legal advice; it still needs review by Dutch
counsel (spec open question 3) and should follow the planned replacement law (Wet VBAR).
Matching is case-insensitive on word boundaries.
"""

from __future__ import annotations

import re

# Employment framing: phrase -> why it's risky
BANNED_PHRASES: dict[str, str] = {
    # English
    "join your team": "positions the freelancer as a team member",
    "join the team": "positions the freelancer as a team member",
    "part of your team": "positions the freelancer as a team member",
    "extension of your team": "positions the freelancer as a team member",
    "staff augmentation": "describes hiring capacity, not a project",
    "full-time": "employment hours framing",
    "fulltime": "employment hours framing",
    "40 hours": "hours-based framing",
    "hours per week": "hours-based framing",
    "hours a week": "hours-based framing",
    "under your supervision": "working under the client's direction",
    "report to your": "working under the client's direction",
    "available immediately for a long-term": "open-ended engagement",
    "ongoing support": "open-ended engagement",
    "on an hourly basis": "hours-based framing",
    "as an employee": "employment framing",
    # Dutch
    "versterking van je team": "positions the freelancer as a team member",
    "versterking van uw team": "positions the freelancer as a team member",
    "onderdeel van je team": "positions the freelancer as a team member",
    "onderdeel van uw team": "positions the freelancer as a team member",
    "uur per week": "hours-based framing",
    "uren per week": "hours-based framing",
    "onder leiding van": "working under the client's direction",
    "onder uw leiding": "working under the client's direction",
    "detachering": "describes secondment, not a project",
    "inhuur": "describes hiring capacity, not a project",
    "voor onbepaalde tijd": "open-ended engagement",
    "doorlopende ondersteuning": "open-ended engagement",
    "in loondienst": "employment framing",
}

_PATTERNS = {p: re.compile(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])") for p in BANNED_PHRASES}


def find_banned(text: str) -> list[str]:
    """Banned phrases found in the text, as 'phrase (why)'."""
    low = (text or "").lower()
    return [f"'{p}' ({BANNED_PHRASES[p]})" for p, pat in _PATTERNS.items() if pat.search(low)]


DISCLAIMER = "Guidance only, not legal advice."
