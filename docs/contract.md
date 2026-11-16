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
| POST | `/api/drafts/{id}/approve` | `{final_text}` | Draft (approved; nothing is sent) | — |
| POST | `/api/drafts/{id}/sent` | — | Draft (sent) | — |
| POST | `/api/drafts/{id}/reject` | `{note}` | Draft | — |
| GET | `/api/pipeline` | — | Columns + deadlines | `pipeline.json` |
| POST | `/api/leads/{id}/replied` | `{note?}` (optional) | Lead (replied) | — |
| POST | `/api/leads/{id}/won` | `{amount?, note?}` (optional; amount in USD, ≥ 0) | Lead (won) | — |
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

### After approval (both modes; no email is sent by Chipi)

The user sends an approved draft themselves (copies the text, or uses the apply link), then
tells Chipi. The demo passcode guards these like every other `/api/` write.

- `POST /api/drafts/{id}/sent`: only an **approved** draft; anything else answers **422**
  `blocked` (product rule 1). Sets `status: "sent"` and `sent_at`, finishes its `send` queue
  item, moves the lead to `sent`. Marking a sent draft again returns it unchanged.
- `POST /api/leads/{id}/replied`: lead → `replied`. **409** if nothing was marked sent to
  this lead. Once per lead: a second click (or a lead already won) changes nothing.
- `POST /api/leads/{id}/won`: lead → `won`. **409** if nothing was marked sent. Once per lead.
  A win straight from `sent` also records the reply it implies.
- The Status page finds a card's lead id by nickname in `GET /api/leads`.

Events written (real mode; `lead` is the lead's nickname, `channel` is where the lead came
from: `RemoteOK`, `Remotive`, `HN Who's Hiring`, `Web search`, `Pasted link`, else
`Direct email` / `Apply link`):

```
draft_approved  user  {draft_id, queue_id, lead_id, lead, template}          // no channel: approving isn't sending
draft_sent      user  {draft_id, queue_id, lead_id, lead, channel, via: "email"|"apply_link", template}
reply_received  world {lead, lead_id, draft_id, channel, reported_by: "user", note?, implied_by?: "contract_won"}
contract_won    world {lead, lead_id, draft_id, channel, reported_by: "user", amount?, goal?, note?}
```

`GET /api/metrics` (real mode) reads an approved-then-sent pair as the one `draft_approved
{channel, lead, template}` the simulation writes, timed at the send, so live and simulated
numbers are counted the same way. A `draft_sent` with no approval stays visible as an
unapproved send. After each sent/replied/won the Learner rewrites `wiki/learning.md` and the
block between `<!-- LIVE NUMBERS … -->` and `<!-- /LIVE NUMBERS -->` in the DERIVED part of
`goal.md` (added at the end of DERIVED the first time). The fixed core is never touched.

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

## Proposed: spec v2 Researcher (pending partner OK)

Real mode only. Needs an approved profile and approved services.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/research` | `{limit?: 1-25 (default 10), lead_ids?: string[]}` | 202 `Job` |
| GET | `/api/research/status` | — | `Job` (`result`: researched / dropped / failed counts, searches, scrapes) |
| GET | `/api/leads/{id}` | — | `Lead` with `brief` |

Without `lead_ids`, the "filtered" leads most relevant to the approved services go first
(app/relevance.py; the relevance is saved as the lead's `score`). Researched leads move to
`researched`, non-fits to `dropped` (with `why` = the reason); failures stay `filtered` with
`research_error`. `GET /api/leads` includes `brief` on leads that have one.

```
Brief { fit, fit_reason, service_id|null,
        hook: {summary, source_url, published_at, type: news|blog|launch|funding|leadership|partnership|posting|other,
               raw?: "raw/…" } | null,
        problem_hypothesis, channel: warm_intro|apply_interim|direct_note, warm_connection|null,
        draft_language: nl|en, sources: [{url, title, published_at}],
        provenance: {model, prompt_version, input_ref, ts, cost} }
```

`/mcp`: `research_leads(limit)`; poll `get_job("research")`.

## Proposed: spec v2 Writer + Verifier and the draft view (pending partner OK)

Real mode only. Nothing is ever sent: the freelancer copies a draft and sends it themselves.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/leads/{id}/draft` | `{language?: "nl"\|"en"}` (rewrite in that language) | 202 `Job` (`result: {draft_id, status}`) |
| GET | `/api/leads/{id}/draft/status` | — | `Job` |
| POST | `/api/drafts/write` | `{limit?: 1-25}` (researched leads without a draft) | 202 `Job` |
| GET | `/api/drafts/write/status` | — | `Job` |
| POST | `/api/drafts/{id}/copied` | `{final_text}` | `Draft` (status `copied`; the freelancer's approval) |
| POST | `/api/drafts/{id}/reject` | `{note}` | `Draft` (also for `ready` / `needs_human`) |

Draft statuses add `ready` (passed every check), `needs_human` (still failing after 2 revisions),
`copied` and `superseded` (replaced by a newer draft in the same language). Channels add
`apply_interim`, `warm_intro`, `direct_note`. v2 drafts also carry `language`, `round`,
`forwardable_blurb`, `claims_used`, `hook_source_url`, `service_id`, `final_text`, `copied_at`, and
`verifier: {passed, pass, rounds, checks: [{name, passed, note}], issues, revision_notes, history,
guidance: string[], provenance}`. Copying appends `draft_copied` (and `draft_edited` if the text changed).
`/mcp`: `write_drafts(limit)`; poll `get_job("drafts")`.

## Simulate 2 weeks (`GET /api/metrics?simulate_weeks=true`)

Returns the usual Metrics shape for a generated two-week history, plus `simulated: true`
and `timeline`: one snapshot per day, `[{day, headline, channels, learning, guardrails}]`,
day 0 = before any outreach. Computed once at startup and cached; nothing touches real data.
The minutes headline is a rolling 7 days here, so it doesn't reset mid-animation.
