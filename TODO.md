# Build plan

Owners: **A** = backend + routine, **B** = frontend. Marks: **real** / **fake** (demo).

## Hour 0–1 (together)
- [ ] Agree `docs/contract.md` and `fixtures/` (both)
- [x] Repo on GitHub (YoAr/chipi-ai); add your partner as a collaborator (both)
- [ ] `.env` filled locally; Anthropic spend cap set (A)

## Backend (A)
- [x] FastAPI skeleton serving `fixtures/` at every `/api/*` route (mock mode), 18 tests — unblocks B
- [ ] Data layer: wiki read/write, events append, SQLite (pipeline, queue, settings)
- [ ] Code gates: rate floor, approval-before-send, allowlist, dedup, fetched-post (real)
- [ ] Tools: `ask_wiki`, `ingest`, `compute_pmf`, `suggest_rate` (real)
- [ ] Tools: `search_opportunities` (RemoteOK, Remotive, HN) + `score_leads` (real)
- [ ] Tools: `draft_outreach` + `verify_draft` (real)
- [ ] `/api/chat` streaming with Tool Runner (real)
- [ ] `/mcp` exposing the same tools, token-protected (real)
- [ ] `/hooks/email` inbound replies → event + queue (real if Resend inbound works, else fake)
- [ ] Learner + metrics script (real logic, **fake** 2-week history via simulator)
- [x] Dockerfile + fly.toml (one always-on machine; landing at /, app at /app/, API at /api/) — image tested locally
- [ ] First `fly deploy` (needs `fly auth login`)
- [ ] Fly volume at /data once real data is written (mock mode needs none)
- [ ] Laptop routine: scheduled task + `/loop` pointing at a `/checkin` skill

## Frontend (B)
- [x] Landing page from partner's export at `/`, CTAs → `/app/#onboarding`
- [x] Vite + React app at `/app/`, landing page fonts and colors, reading the mock API (plain CSS, no shadcn)
- [x] Chat panel (streamed)
- [x] Profile: CV upload (mock), persona, hours and income goal
- [x] Fit & rate (with reasons, sources and checks)
- [x] Offers
- [x] Leads list with verdict buttons, paste-a-link
- [x] Draft review: edit, approve, reject
- [x] Pipeline + deadlines
- [x] Metrics: headline numbers, channel ranges, guardrails
- [ ] "Simulate 2 weeks" before/after on Metrics
- [ ] Phone-width pass on every screen

## Evals (A, small)
- [ ] 20 labelled job posts (scorer), 20 labelled replies (triage), 5 leads (writer rubric)
- [ ] `evals/run.py` prints a score table
- [ ] Learner simulation test: finds the real pattern, ignores noise

## Checkpoints
- [ ] Lunch: B switches from fixtures to the live backend
- [ ] Late afternoon: full run-through
- [ ] **Freeze 2 h before demo**: bug fixes only, record the backup video, snapshot `/data`
