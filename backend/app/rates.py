"""Rate benchmarks: data_dir/benchmarks.json, refreshed from real market data.

`refresh_benchmarks` fetches ZZPdock's published freelance rates for each vakgebied,
saves the extracted figures as a new raw/ file (the citation every rate reason points
to), and rewrites benchmarks.json. If a page fails, its previous entry is kept and the
failure is reported. Nothing is estimated or filled in.

Called by the routine through the /mcp tool `refresh_rate_benchmarks` (daily is plenty;
ZZPdock refreshes hourly).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .sources import rates_zzpdock
from .storage.wiki import save_raw

FILENAME = "benchmarks.json"
# Tech and data freelancers (spec v2 MVP segment). ZZPdock has no separate "data" rates page yet.
DEFAULT_SLUGS = ("ict",)

ABOUT = ("Dutch freelance (zzp) hourly rates, EUR excl. VAT, published on assignments and collected "
         "by ZZPdock (CC BY 4.0). low/high = middle half (P25-P75); typical = middle value; n = "
         "assignments with a published rate. Each entry cites the raw/ extract it came from.")


def load_benchmarks(data_dir: Path) -> dict:
    path = Path(data_dir) / FILENAME
    if not path.is_file():
        return {"hourly": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_atomic(path: Path, obj: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def refresh_benchmarks(data_dir: Path, client: httpx.Client | None = None,
                       slugs: tuple[str, ...] = DEFAULT_SLUGS) -> dict:
    """Refresh benchmarks.json. Returns {"updated": [...], "failed": [{slug, error}]}."""
    data_dir = Path(data_dir)
    previous = {e.get("vakgebied"): e for e in load_benchmarks(data_dir).get("hourly", [])
                if isinstance(e, dict)}
    entries, updated, failed = dict(previous), [], []
    own_client = client is None
    client = client or httpx.Client(follow_redirects=True)
    try:
        for slug in slugs:
            try:
                entry = rates_zzpdock.fetch(client, slug)
            except (httpx.HTTPError, ValueError) as exc:
                failed.append({"slug": slug, "error": f"{type(exc).__name__}: {exc}"[:300]})
                continue
            raw = save_raw(data_dir, f"zzpdock-{slug}.json",
                           json.dumps(entry, ensure_ascii=False, indent=2).encode())
            entries[slug] = {**entry, "raw": raw}
            updated.append(slug)
    finally:
        if own_client:
            client.close()
    if updated:
        _write_atomic(data_dir / FILENAME, {
            "_about": ABOUT,
            "market": "NL",
            "currency": "EUR",
            "unit": "hour",
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hourly": [entries[k] for k in sorted(entries) if k],
        })
    return {"updated": updated, "failed": failed}
