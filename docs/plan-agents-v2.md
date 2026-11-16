# Plan: freelancer agents (spec v2)

Implements `docs/freelancer-agents-spec-v2.md` on branch `feature/freelancer-agents-v2`.
Status: **draft, waiting for approval** (spec §0 step 3).

## Decisions already made (2026-11-15)

| Topic | Decision |
|---|---|
| Direction | Replace the current flow in place: Amsterdam / NL, EUR, Recruitee leads, drafts copied and sent by the freelancer. |
| Search | Oxylabs only (`SearchProvider`). Without credentials, Scout and Researcher jobs fail with "Oxylabs is not configured". |
| Scope | All 6 agents, Scout, demand index, Verifier pre-checks, Tracker, plus the minimal UI needed to drive them. |
| Rate floor | The **approved profile** owns it. CLAUDE.md rule 2 and `guards.py` change to read it from there. |
| Models | Stay on OpenAI (CLAUDE.md wins; the spec says to map tiers onto the repo's client). Judgment → `smart`, Standard → `check`, Fast → `fast`. |

## How spec concepts map onto the repo

| Spec | Lives in | Reuses |
|---|---|---|
| Agents (§5) | `backend/app/ai/{interviewer,offers,researcher,writer,verifier,learner}.py` | `ai.structured(...)` with Pydantic models matching each output contract; validate, retry once, then fail the job (§2.2) |
| Provenance (§2.3) | A `provenance` object `{model, prompt_version, input_ref, ts}` on every agent output | Prompt versions as constants in each agent module |
| Tools once, exposed twice | `backend/app/tools/`: one registry used by `/api/chat` (read-only tools) and `/mcp` (routine tools) | Replaces the hand-written list in `ai/chat.py`; fixes CLAUDE.md's `backend/tools/` path |
| Providers (§7) | `backend/app/providers/{search_oxylabs,fetcher}.py` and `backend/app/sources/recruitee.py` (`JobSource`) | `httpx`; Oxylabs **Web API** (`POST webapi.oxylabs.io/v1/search` and `/v1/scrape`, `Authorization: Bearer $OXYLABS_WEB_API_KEY`), in `.env` and as a Fly secret. Verified 2026-11-15: `site:recruitee.com` queries with `location: "NL"` work (20 results → 12 company slugs). The search has **no recency filter and no publish dates**, so `freshness_days` is enforced in code from dates on the fetched page |
| Scout (§6.1) | `backend/app/scout.py` | Existing `normalize.py` (dedup, strip_html); ≤ 1 request/s with backoff |
| Demand index (§6.2) | `backend/app/demand.py` (pure code) | `metric_snapshots` pattern; new `posting_snapshots` table |
| Verifier pre-checks (§6.4) | `backend/app/ai/prechecks.py`: plain functions with unit tests | `guards.py` style |
| Tracker (§6.3) | `RealStore` status transitions + `events.jsonl` (append-only) | Status changes are events; the funnel comes from `metrics.py` |
| Background work | Existing `jobs.py` (202 + poll) for user-triggered runs; `/mcp` tools for the daily routine (Scout, panel snapshot, reminders) | "Model runs from the queue": the routine calls the tools, and the app does the work |

### Data (SQLite, additive migrations only: Fly already holds real data)

- `docs`: `profile` and `services` replace `persona`, `fit` and `offers`. A new `doc_versions` table keeps every version with its approval state and provenance.
- `leads`: new columns `source_posting_id` (unique), `company_slug`, `department`, `location`, `remote`, `employment_type`, `language`, `published_at`, `description`, `apply_url`, `skills_json`. Statuses follow §6.3.
- `drafts`: new columns `language`, `round`, `forwardable_blurb`, `claims_json`, `hook_source_url`. Statuses: `needs_human | ready | copied | rejected`.
- New tables: `companies`, `briefs`, `engagements`, `meetings` (with `consent_confirmed`), `learnings`, `posting_snapshots`.
- Old rows stay readable; RemoteOK, Remotive and HN fetchers and `score_leads` are removed from the flow.

## API changes (needs your partner's OK: CLAUDE.md "Contract")

New or changed routes, all 202 + poll where a model is involved:

- `POST /api/interview` (one turn, streamed) · `GET/PUT /api/profile` · `POST /api/profile/approve`
- `POST /api/services/build` · `GET /api/services` · `POST /api/services/approve`
- `POST /api/scout` · `GET /api/leads?status=` (with brief) · `POST /api/leads/{id}/research`
- `POST /api/leads/{id}/draft` (Writer ⇄ Verifier) · `POST /api/drafts/{id}/regenerate {language}` · `POST /api/drafts/{id}/copied`
- `POST /api/leads/{id}/status {status}` (Tracker one-click actions) · `GET /api/reminders`
- `POST /api/meetings` (transcript + consent) · `GET /api/learnings` · `POST /api/learnings/{id}/approve`
- Removed: the draft approve → email send path. Rule 4 (the allowlist) stays in code, but nothing calls it.

`docs/contract.md` and `fixtures/` get updated in the same commit, as CLAUDE.md requires.

## Minimal UI (same React app and styles)

1. **Your profile** → interview chat with a progress line, plus a profile summary with an Approve button.
2. **Fit & rate + Offers** → one **Services** screen: services, demand index with its explanation, Approve.
3. **Leads** → brief (hook, matched service, channel), status filter, one-click status buttons.
4. **Drafts** → Copy button, NL/EN toggle, posting and apply links, Verifier notes, compliance guidance and the "not legal advice" footer.
5. **Metrics** → funnel, plus Learner proposals with Approve. The reminder inbox goes on **Today**.

## Build order (spec §9), each ending with its acceptance check as a test

1. Groundwork: schema migration, `tools/` registry, providers, EUR/Amsterdam switch (goal.md, benchmarks, copy), CLAUDE.md rule 2.
2. Scout + baseline panel (M2)
3. Interviewer + profile approval (M3)
4. Demand index + Offer designer (M4)
5. Researcher (M5)
6. Writer + Verifier (M6)
7. Draft view + Tracker (M7)
8. Learner + insights (M8)

Tests never call OpenAI or Oxylabs: they monkeypatch `ai.structured` and the providers, and replay recorded Recruitee responses.

## Things I need from you, or will flag in the PR

1. **`uv`**: not installed here, so I can't run the backend tests. `brew install uv`.
2. **Oxylabs credentials** and a check of its terms and cost (spec open question 4). I'll verify the endpoint and parameters against Oxylabs' docs, but can't call it live without a key.
3. **EUR rate benchmarks** (open question 5): **done.** They come from ZZPdock market data (CC BY 4.0): rates published on Dutch freelance assignments, refreshed hourly, with the middle value, P25–P75 and n per role. `app/sources/rates_zzpdock.py` parses the page's schema.org `Dataset` block and its role table. `app/rates.py` saves each extract to `raw/` and rewrites `benchmarks.json`; the `/mcp` tool `refresh_rate_benchmarks` runs it. Gaps: there's no "Data" vakgebied page and no Amsterdam split, and only ~1 in 3 assignments publishes a rate.
4. **CBS sector signal** (open question 2): its weight stays 0, with the 0.2 moved to the posting signal, until a CBS table is verified.
5. **Baseline panel**: built from broad Oxylabs searches (not tied to the freelancer's skills). It will start well under 500 companies, which is fine, since the spec plans a cold start.
6. **Wet DBA banned phrases** (NL + EN): I'll write a starter list in config. Counsel review stays open (open question 3).
7. **Contract**: your partner agrees to the API changes above before merge.
