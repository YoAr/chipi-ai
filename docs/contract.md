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

## Simulate 2 weeks (`GET /api/metrics?simulate_weeks=true`)

Returns the usual Metrics shape for a generated two-week history, plus `simulated: true`
and `timeline`: one snapshot per day, `[{day, headline, channels, learning, guardrails}]`,
day 0 = before any outreach. Computed once at startup and cached; nothing touches real data.
The minutes headline is a rolling 7 days here, so it doesn't reset mid-animation.
