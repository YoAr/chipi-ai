"""Turn the partner's landing page export into the page served at "/".

The export is a self-unpacking bundle: the page's HTML lives as a JSON string in
<script type="__bundler/template">. This rewrites the sign-up and log-in links in
that template to point at the app, and leaves everything else untouched.

    python3 scripts/build_landing.py

Re-run it whenever the partner sends a new export (save it over
frontend/landing/chipi-homepage.export.html first).
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend/landing/chipi-homepage.export.html"
OUT = ROOT / "frontend/landing/partner-landing.html"  # old bundle page; "/" serves the hand-built index.html

# Landing-page anchor -> where it should go in the app
LINKS = {
    'href="#upload"': 'href="/app/#onboarding"',  # "Upload your CV" buttons
    'href="#start"': 'href="/app/#onboarding"',   # nav "Upload your CV"
    'href="#login"': 'href="/app/"',              # "Log in"
}

html = SRC.read_text()
match = re.search(r'(<script type="__bundler/template">)(.*?)(</script>)', html, re.S)
if not match:
    raise SystemExit("No __bundler/template block found; is this the right export?")

template = json.loads(match.group(2))
for old, new in LINKS.items():
    count = template.count(old)
    template = template.replace(old, new)
    print(f"{old} -> {new}: {count} link(s)")

# Re-encode; escape "</" so the string can't close the surrounding <script> early.
encoded = json.dumps(template).replace("</", "<\\u002F")
OUT.write_text(html[: match.start(2)] + encoded + html[match.end(2):])
print(f"Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size:,} bytes)")
