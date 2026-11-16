# API contract

Both halves build against this. Every `GET` has a matching file in `fixtures/`
that the backend serves when `MOCK_MODE=1`. Change a shape only with both people
agreed, and update the fixture in the same commit.

All responses are JSON. Errors: `{"error": {"code": "…", "message": "…"}}`.

## UI API (`/api/*`)

| Method | Path | Body | Returns | Fixture |
|---|---|---|---|---|
| GET | `/api/state` | — | Header data for every screen | `state.json` |
| GET | `/api/persona` | — | Persona | `persona.json` |
| PUT | `/api/persona` | Persona fields | Persona | — |
| GET | `/api/fit` | — | Fits + rate with reasons | `fit.json` |
| GET | `/api/offers` | — | Three offers | `offers.json` |
| GET | `/api/leads?status=new` | — | Ranked leads | `leads.json` |
| POST | `/api/leads/{id}/verdict` | `{verdict: "useful"\|"maybe"\|"skip", note?}` | Lead | — |
| POST | `/api/seeds` | `{url}` | Lead (scored later) | — |
| GET | `/api/drafts?status=pending` | — | Drafts awaiting approval | `drafts.json` |
| POST | `/api/drafts/{id}/approve` | `{final_text}` | Draft (sent) | — |
| POST | `/api/drafts/{id}/reject` | `{note}` | Draft | — |
| GET | `/api/pipeline` | — | Columns + deadlines | `pipeline.json` |
| GET | `/api/metrics` | — | Headline numbers + groups | `metrics.json` |
| POST | `/api/chat` | `{messages: [{role, content}]}` | Server-sent events stream | — |
| POST | `/api/reset` | header `Authorization: Bearer $MCP_TOKEN` | `{ok: true}` | — |

### Shapes (field names are the contract; see fixtures for full examples)

```
Lead     { id, nickname, title, company, source, url, fetched: bool,
           rate_text, score: 0-100, why, status: new|useful|maybe|skip|drafted|sent|replied|won|lost,
           found_at }
Draft    { id, lead_id, nickname, channel: "email"|"apply_link", to, subject, body,
           verifier: { passed: bool, checks: [{name, passed, note}] },
           status: pending|approved|sent|rejected, created_at }
Rate     { floor, ask, low, high, currency: "USD", unit: "hour",
           reasons: [{ text, source }] }
Metric   { name, value, low, high, n, direction: "up"|"down" }   // low/high = range; n = sample size
```

### Chat stream (`POST /api/chat`)

Server-sent events, one JSON object per `data:` line:

```
{"type":"text","delta":"…"}
{"type":"tool","name":"suggest_rate","status":"running"|"done"}
{"type":"done"}
{"type":"error","message":"…"}
```

## Routine API (`/mcp`, token required)

The same tool functions the chat uses, plus queue tools:

`ask_wiki` · `ingest` · `lint` · `compute_pmf` · `suggest_rate` · `generate_offers` ·
`search_opportunities` · `score_leads` · `draft_outreach` · `verify_draft` ·
`get_metrics` · `record_lesson` · `get_queue` · `complete_queue_item`

## Inbound email (`POST /hooks/email`)

Resend inbound webhook. Appends a `reply_received` event and queues triage.

## AI actions (real mode only; added for live mode)

All of these answer **409** in sample-data mode, **429** past 10 actions per minute,
and **503** `model_unavailable` if OpenAI can't be reached. Long work runs in the
background: the POST returns **202** at once, and the UI polls the matching status.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/profile/build` | — | 202 `Job` |
| GET | `/api/profile/status` | — | `Job` |
| POST | `/api/search` | `{keywords?: string[]}` | 202 `Job` |
| GET | `/api/search/status` | — | `Job` |
| POST | `/api/leads/{id}/draft` | — | 202 `Job` |
| GET | `/api/leads/{id}/draft/status` | — | `Job` (`result.draft_id` when done) |

```
Job { status: "idle" | "running" | "done" | "error",
      step: string | null,        // e.g. "persona" → "fit" → "offers"; "fetching" → "scoring"
      message: string | null,     // short, user-facing ("Found 23 leads, added 8")
      error: string | null,
      result: object | null,
      updated_at: string | null }
```

- `POST /api/cv` (unchanged shape) also records the CV's text path; the UI then calls
  `POST /api/profile/build`, which runs persona → fit & rate → offers and fills
  `/api/persona`, `/api/fit`, `/api/offers` in the existing shapes.
- `POST /api/search` fetches RemoteOK, Remotive and HN, de-duplicates, scores each new
  lead (0–100 with a one-line `why`), and adds them to `/api/leads`.
- `POST /api/leads/{id}/draft` writes an outreach draft, runs the verifier (code checks
  plus a fresh-context model check), and adds it to `/api/drafts?status=pending` with its
  `verifier.checks`.
- `GET /api/state` → `mode` is `"real"` or `"mock"`; the UI hides AI buttons in `"mock"`.

## Proposed: spec v2 Scout (pending partner OK; see docs/plan-agents-v2.md)

Real mode only (409 with sample data). 202 + poll, same `Job` shape as the AI actions.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/scout` | `{terms?: string[]}` (≤ 10; default: approved services' discovery terms, else profile skills) | 202 `Job` |
| GET | `/api/scout/status` | — | `Job` (`result`: counts per step) |
| POST | `/api/panel/snapshot` | — | 202 `Job` |
| GET | `/api/panel/snapshot/status` | — | `Job` |

Scout leads have `status: "filtered"` and these extra fields (null on older leads):

```
Lead (v2 additions) { source_posting_id, company_slug, department, location, remote: bool|null,
                      employment_type, language: "nl"|"en", published_at, description, apply_url,
                      skills: string[] }
```

`/mcp` routine tools: `run_scout(terms?)` · `snapshot_panel()` · `get_job(key)` · `refresh_rate_benchmarks()`.
`503 provider_unavailable` when Oxylabs can't be used (e.g. `OXYLABS_WEB_API_KEY` missing).

## Proposed: spec v2 Interviewer and profile (pending partner OK)

Real mode only. Interview turns answer directly; building the profile is a background job.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/interview` | — | `Interview` |
| POST | `/api/interview/start` | `{language: "nl"\|"en", segment?}` (needs an uploaded CV) | `Interview` with the first question |
| POST | `/api/interview/answer` | `{answer}` (1–4000 chars) | `Interview` (next question; when complete, the profile job starts) |
| POST | `/api/interview/finish` | — | 202 `Interview` (ends early / rebuilds; profile job starts) |
| GET | `/api/profile` | — | `{latest: ProfileVersion\|null, approved: ProfileVersion\|null}` |
| PUT | `/api/profile` | `Profile` (spec §5.1 contract) | `ProfileVersion` (new draft, author "user") |
| POST | `/api/profile/approve` | `{version}` | `ProfileVersion` (409 if no rate floor or not a draft) |

```
Interview { status: "not_started"|"active"|"complete"|"extracted", language, segment,
            turns: [{question, resume_line|null, reflection, topic, answer|null}],
            progress: {asked, left, max: 15} | null, transcript_ref, job: Job }
ProfileVersion { version, status: "draft"|"approved"|"superseded", author: "agent"|"user",
                 data: Profile, checks: [{name, passed, note}],
                 provenance: {model, prompt_version, input_ref, ts}, created_at, approved_at }
```

Approving sets the rate floor (EUR/hour) every price check uses, hands the exclusions to Scout,
updates `/api/persona` (skills, wants, avoids, hours) and appends a `profile_approved` event.
`/api/fit` `rate.floor` is null until a profile is approved.

## Proposed: spec v2 Offer designer and services (pending partner OK)

Real mode only. Needs an approved profile.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/services/build` | — | 202 `Job` |
| GET | `/api/services/status` | — | `Job` (`result.version` when done) |
| GET | `/api/services` | — | `{latest: ServicesVersion\|null, approved: ServicesVersion\|null}` |
| PUT | `/api/services` | `{services: Service[]}` (1–5) | `ServicesVersion` (new draft, author "user"; 409 below the floor) |
| POST | `/api/services/approve` | `{version}` | `ServicesVersion` |

```
Service { id, name, target_buyer, problem, deliverable, duration_weeks, skills_used: string[],
          rate_range_eur: {min, max}, demand_index: 0-1, proof_strength, independence_rationale,
          rationale, discovery_terms: {nl: string[], en: string[]},
          demand: {value, posting_signal, best: {skills, count, n, share, lift, premium,
                   contract_share, trend, signal}, proof_factor, weights, no_market_data, explanation} }
ServicesVersion = ProfileVersion with data: {services: Service[], market: <demand inputs table>}
```

`demand_index` and `proof_strength` are computed in code (app/demand.py), never by the model.
Approving hands the services' NL and EN discovery terms to Scout (`POST /api/scout` default) and
appends a `services_approved` event. The Offers tab is labelled "Services"; in sample-data mode
it still shows `/api/offers`.
