"""Dutch freelance hourly rates from ZZPdock market data (CC BY 4.0).

Source pages: https://zzpdock.nl/marktdata/tarieven/{slug} (refreshed hourly by ZZPdock).
There is no API (robots.txt disallows /api/); /marktdata/ pages are allowed. Each page has:
- a schema.org `Dataset` JSON-LD block whose description holds the headline figures
  ("gangbaar uurtarief van €100, ... tussen €95 en €120, op basis van 222 opdrachten") and
  `dateModified`;
- a table of rates per role: Rol | Opdrachten met tarief | Onderkant | Gangbaar | Bovenkant.

Figures are rates published on assignments (excl. VAT), shown as the middle value and the
middle half (P25–P75). Only ~1 in 3 assignments publishes a rate, so treat them as a sample.

`parse` raises RatesPageChanged rather than guessing when the page doesn't match, so no
number ever reaches the app that wasn't on the page (CLAUDE.md rule 3).
"""

from __future__ import annotations

import html as html_lib
import json
import re
from datetime import datetime, timezone

import httpx

BASE = "https://zzpdock.nl/marktdata/tarieven/"
HEADERS = {"User-Agent": "chipi-ai/0.1 (freelance rate benchmarks)"}
LICENCE = "CC BY 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"

_SLUG_RE = re.compile(r"^[a-z0-9-]{1,40}$")
_LD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_EUR = r"€\s?(\d{1,4})"
_HEADLINE_RE = re.compile(
    rf"gangbaar uurtarief van {_EUR}.*?tussen {_EUR} en {_EUR}.*?(\d{{1,6}}) opdrachten", re.S | re.I
)
_ROLE_HEADER = ["rol", "opdrachten met tarief", "onderkant", "gangbaar", "bovenkant"]


class RatesPageChanged(ValueError):
    """The page no longer has the structure this parser expects."""


def page_url(slug: str) -> str:
    if not _SLUG_RE.match(slug or ""):
        raise ValueError(f"Bad ZZPdock slug {slug!r}")
    return BASE + slug


def _cell(text: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _eur(text: str) -> int:
    m = re.fullmatch(_EUR, text.strip())
    if not m:
        raise RatesPageChanged(f"Expected a euro amount, got {text!r}")
    return int(m.group(1))


def _dataset(page: str) -> dict:
    for block in _LD_RE.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Dataset":
            return data
    raise RatesPageChanged("No schema.org Dataset block on the page")


def _roles(page: str) -> list[dict]:
    """The per-role table, or [] if the page has none (small vakgebieden)."""
    for table in re.findall(r"<table.*?</table>", page, re.S):
        rows = [[_cell(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, re.S)]
                for r in re.findall(r"<tr.*?</tr>", table, re.S)]
        if not rows or [h.lower() for h in rows[0]] != _ROLE_HEADER:
            continue
        out = []
        for row in rows[1:]:
            if len(row) != 5:
                raise RatesPageChanged(f"Role row has {len(row)} cells: {row!r}")
            out.append({"role": row[0], "n": int(row[1]), "low": _eur(row[2]),
                        "typical": _eur(row[3]), "high": _eur(row[4])})
        return out
    return []


def parse(page: str, slug: str) -> dict:
    """Extract the figures from one ZZPdock rates page. Raises RatesPageChanged."""
    data = _dataset(page)
    m = _HEADLINE_RE.search(data.get("description") or "")
    if not m:
        raise RatesPageChanged("Dataset description has no headline rate")
    typical, low, high, n = (int(g) for g in m.groups())
    if not low <= typical <= high:
        raise RatesPageChanged(f"Headline figures out of order: {low} ≤ {typical} ≤ {high}")
    url = page_url(slug)
    accessed = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    name = (data.get("name") or slug).split("|")[0].strip()
    return {
        "source": f"ZZPdock, {name}",
        "vakgebied": slug,
        "url": url,
        "kind": "published_assignment_rates",
        "currency": "EUR",
        "unit": "hour",
        "vat": "excluded",
        "typical": typical,
        "low": low,     # bottom of the middle half (P25)
        "high": high,   # top of the middle half (P75)
        "n": n,         # assignments with a published rate
        "roles": _roles(page),
        "date_modified": data.get("dateModified"),
        "accessed_at": accessed,
        "licence": LICENCE,
        "attribution": (f"ZZPdock, {name}. Geraadpleegd op {accessed}. Beschikbaar op {url} "
                        f"({LICENCE}). Figures extracted unchanged."),
    }


def fetch(client: httpx.Client, slug: str) -> dict:
    """Fetch and parse one vakgebied. Raises httpx.HTTPError or RatesPageChanged."""
    r = client.get(page_url(slug), headers=HEADERS, timeout=15)
    r.raise_for_status()
    return parse(r.text, slug)
