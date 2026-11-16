# Events

`data/events.jsonl` is append-only: one JSON object per line, never edited.
It is the raw truth that lessons and metrics are computed from.

```json
{"id":"ev_0192","ts":"2026-11-20T14:02:00Z","type":"draft_edited","actor":"user",
 "subject":"lead:payments-api","payload":{"before":"…","after":"…","template":"v3"}}
```

| Field | Meaning |
|---|---|
| `id` | `ev_` + sequence |
| `ts` | UTC, ISO 8601 |
| `type` | One of the types below |
| `actor` | `user` · `world` · `agent` |
| `subject` | What it's about: `lead:<nickname>`, `draft:<id>`, `goal`, `persona` |
| `payload` | Type-specific; always include a `note` when the user gave a reason |

## Types

**From the user:** `lead_verdict` · `draft_edited` · `draft_approved` ·
`draft_rejected` · `rate_decision` · `steering_note` · `seed_added` ·
`goal_core_edited` · `persona_updated` · `profile_approved` · `services_approved`

**From the world:** `reply_received` · `no_reply_timeout` · `call_booked` ·
`contract_won` · `contract_lost` · `milestone_due` · `invoice_paid`

**From agents:** `lead_found` · `lead_researched` · `lead_dropped` · `draft_created` · `verifier_failed` ·
`lesson_added` · `lesson_applied` · `run_completed` · `run_failed`

## What each event triggers

| Event | Instantly (code) | Queued (model work, via the routine) |
|---|---|---|
| `reply_received` | Attach to lead, move in pipeline | Triage → draft response (urgent) |
| `draft_edited` | Save version | Voice update after 3 edits; A/B the new template |
| `seed_added` | Fetch the post | Score it now |
| `lead_verdict` | Update lead precision | Daily Learner run |
| `rate_decision` | Log | Re-check the ask at the daily run |
| `contract_won` | Pipeline, deadline timers | Reweight offers |
| `goal_core_edited` | Lock the new floor | Full goal recompute now |
