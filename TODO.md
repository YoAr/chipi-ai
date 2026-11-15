# Build plan

Owners: **A** = backend + routine, **B** = frontend. Marks: **real** / **fake** (demo).

## Hour 0–1 (together)
- [ ] Agree `docs/contract.md` and `fixtures/` (both)
- [ ] Repo on GitHub, both have access (both)
- [ ] `.env` filled locally; Anthropic spend cap set (A)

## Backend (A)
- [ ] FastAPI skeleton serving `fixtures/` at every `/api/*` route (mock mode) — unblocks B
- [ ] Data layer: wiki read/write, events append, SQLite (pipeline, queue, settings)
- [ ] Code gates: rate floor, approval-before-send, allowlist, dedup, fetched-post (real)
- [ ] Tools: `ask_wiki`, `ingest`, `compute_pmf`, `suggest_rate` (real)
- [ ] Tools: `search_opportunities` (RemoteOK, Remotive, HN) + `score_leads` (real)
- [ ] Tools: `draft_outreach` + `verify_draft` (real)
- [ ] `/api/chat` streaming with Tool Runner (real)
- [ ] `/mcp` exposing the same tools, token-protected (real)
- [ ] `/hooks/email` inbound replies → event + queue (real if Resend inbound works, else fake)
- [ ] Learner + metrics script (real logic, **fake** 2-week history via simulator)
- [ ] Fly deploy: Dockerfile, volume, always-on machine
- [ ] Laptop routine: scheduled task + `/loop` pointing at a `/checkin` skill

## Frontend (B)
- [ ] Vite + React + shadcn scaffold, reading fixtures
- [ ] Chat panel (streamed)
- [ ] Persona + fit & rate (with reasons and sources)
- [ ] Offers
- [ ] Leads list with verdict buttons, paste-a-link
- [ ] Draft review: edit, approve, reject
- [ ] Pipeline + deadlines
- [ ] Metrics: two headline numbers, ranges, before/after "simulate 2 weeks"

## Evals (A, small)
- [ ] 20 labelled job posts (scorer), 20 labelled replies (triage), 5 leads (writer rubric)
- [ ] `evals/run.py` prints a score table
- [ ] Learner simulation test: finds the real pattern, ignores noise

## Checkpoints
- [ ] Lunch: B switches from fixtures to the live backend
- [ ] Late afternoon: full run-through
- [ ] **Freeze 2 h before demo**: bug fixes only, record the backup video, snapshot `/data`
