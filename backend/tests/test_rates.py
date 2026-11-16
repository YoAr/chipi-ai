"""ZZPdock rates parser and benchmarks refresh. Fictional figures; no network."""

import json

import httpx
import pytest

from app.rates import load_benchmarks, refresh_benchmarks
from app.sources import rates_zzpdock
from app.sources.rates_zzpdock import RatesPageChanged, parse

DATASET = {
    "@context": "https://schema.org", "@type": "Dataset", "name": "ZZP-tarief ICT | ZZPdock",
    "description": ("Een zzp'er of freelancer in ICT rekent op ZZPdock een gangbaar uurtarief van €90, "
                    "met de meeste tarieven tussen €80 en €110, op basis van 150 opdrachten met tarief."),
    "dateModified": "2026-11-15T10:00:00.000Z",
}
TABLE = """<table><thead><tr><th>Rol</th><th>Opdrachten met tarief</th><th>Onderkant</th>
<th>Gangbaar</th><th>Bovenkant</th></tr></thead><tbody>
<tr><td><a href="#">Developer</a></td><td>40</td><td>€85</td><td>€95</td><td>€115</td></tr>
<tr><td>Architect</td><td>12</td><td>€100</td><td>€112</td><td>€125</td></tr>
</tbody></table>"""


def page(dataset=DATASET, table=TABLE) -> str:
    return (f'<html><script type="application/ld+json">{{"@type": "WebSite"}}</script>'
            f'<script type="application/ld+json">{json.dumps(dataset, ensure_ascii=False)}</script>'
            f"<body>{table}</body></html>")


def test_parse_headline_and_roles():
    out = parse(page(), "ict")
    assert (out["typical"], out["low"], out["high"], out["n"]) == (90, 80, 110, 150)
    assert out["currency"] == "EUR" and out["licence"] == "CC BY 4.0"
    assert out["url"] == "https://zzpdock.nl/marktdata/tarieven/ict"
    assert out["source"] == "ZZPdock, ZZP-tarief ICT"
    assert out["roles"][0] == {"role": "Developer", "n": 40, "low": 85, "typical": 95, "high": 115}
    assert out["date_modified"] == "2026-11-15T10:00:00.000Z"
    assert "zzpdock.nl/marktdata/tarieven/ict (CC BY 4.0)" in out["attribution"]


def test_parse_page_without_role_table():
    assert parse(page(table=""), "ict")["roles"] == []


@pytest.mark.parametrize("broken", [
    page(dataset={"@type": "WebSite"}),                                  # no Dataset block
    page(dataset={**DATASET, "description": "Geen tarieven bekend."}),   # no headline
    page(dataset={**DATASET, "description": DATASET["description"].replace("€90", "€200")}),  # out of order
    page(table=TABLE.replace("<td>€95</td>", "<td>op aanvraag</td>")),   # not a euro amount
])
def test_changed_page_is_refused_not_guessed(broken):
    with pytest.raises(RatesPageChanged):
        parse(broken, "ict")


def test_bad_slug_refused():
    with pytest.raises(ValueError):
        rates_zzpdock.page_url("../api")


def _client(status=200, body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/marktdata/tarieven/")
        return httpx.Response(status, text=body if body is not None else page())
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_refresh_writes_benchmarks_and_raw_citation(tmp_path):
    result = refresh_benchmarks(tmp_path, _client())
    assert result == {"updated": ["ict"], "failed": []}
    bench = load_benchmarks(tmp_path)
    assert bench["currency"] == "EUR" and bench["market"] == "NL"
    entry = bench["hourly"][0]
    assert entry["typical"] == 90 and entry["raw"].startswith("raw/") and entry["raw"].endswith("zzpdock-ict.json")
    cited = json.loads((tmp_path / entry["raw"]).read_text())
    assert cited["typical"] == 90 and cited["url"] == entry["url"]


def test_refresh_failure_keeps_previous_figures(tmp_path):
    refresh_benchmarks(tmp_path, _client())
    before = (tmp_path / "benchmarks.json").read_text()
    result = refresh_benchmarks(tmp_path, _client(status=503))
    assert result["updated"] == [] and result["failed"][0]["slug"] == "ict"
    assert (tmp_path / "benchmarks.json").read_text() == before


def test_refresh_changed_page_reports_failure(tmp_path):
    result = refresh_benchmarks(tmp_path, _client(body="<html>redesigned</html>"))
    assert result["failed"][0]["error"].startswith("RatesPageChanged")
    assert not (tmp_path / "benchmarks.json").exists()
