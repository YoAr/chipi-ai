# Freelancer Work Engine: MVP Spec (v2)

## 0. How to use this spec (instructions for Claude Code)

This spec defines **what** to build: the pipeline, agent contracts and rules. It deliberately does **not** prescribe stack, folder layout, database schema or libraries.

Before writing code:
1. Explore the existing repo: language, frameworks, data layer, job/queue system, LLM client, UI, config and test conventions.
2. Map every concept below onto existing patterns. Reuse existing infrastructure; don't create parallel versions of things the repo already has (DB access, HTTP clients, prompt management, logging).
3. Write a short implementation plan (where each component lives, which existing modules it extends, new dependencies if any) and get approval before building.
4. Build milestone by milestone (§9). Each milestone ends with its acceptance check passing.

Treat anything marked **(verify)** as an assumption to confirm against live docs or responses.

---

## 1. Product summary

A service for independent freelancers in **Amsterdam**. It turns a resume into sellable services ranked by hiring demand, finds companies with a current need for those services, and **drafts** short, highly personalized outreach that the freelancer copies and sends themselves. It learns from each client engagement to sharpen the freelancer's offers.

**MVP scope**
| Dimension | MVP choice |
|---|---|
| Market | Amsterdam / Netherlands |
| Freelancer segment | Tech and data freelancers (default; see open questions) |
| Target sectors | ICT, specialist business services |
| Lead source | Recruitee public job boards |
| Search provider | Oxylabs (search results API) |
| Outreach | Drafts in the app only. No email sending or inbox access |
| Languages | Dutch and English drafts |

**Core premise to validate:** a company hiring for a role will respond to a freelancer offering to cover the need now, as a clearly scoped, independent project.

---

## 2. Principles and constraints

1. **Human gates.** The freelancer approves their profile, their services, and every draft before using it, plus any change the Learner proposes. The system never contacts anyone.
2. **Structured outputs.** Every agent returns JSON matching its contract (§5). Validate; retry once on failure; then surface an error.
3. **Provenance.** Store with every agent output: model, prompt version, input reference, timestamp. Every claim about a company carries a source URL and date.
4. **Wet DBA-aware framing.** Since 1 January 2025 the Dutch tax authority enforces against disguised employment, and many clients now avoid freelancers. Services and drafts must read as **independent, project-based work with defined deliverables**. Never position the freelancer as an extra team member working set hours under supervision.
5. **Compliance guidance, not enforcement.** The freelancer sends messages and owns that decision. The app shows guidance (§6.5). Nothing in the product is legal advice.
6. **Privacy.** Store company-level data and published postings. Do not store personal profiles of individuals (e.g., LinkedIn profile data). Search results about people are used as pointers only.

---

## 3. Pipeline

```
Resume ─► Interviewer ─► Profile ─(approve)─► Offer designer ◄── Demand index (code)
                                                   │
                                      Services + discovery terms ─(approve)
                                                   │
                                           Scout (code: Recruitee)
                                                   │
                                                 Leads
                                                   │
                                     Researcher ─► Brief (or drop lead)
                                                   │
                                     Writer ⇄ Verifier (max 2 revisions)
                                                   │
                         Draft in app ─► freelancer copies & sends ─► logs outcome
                                                   │
                           Tracker (code: statuses, reminders, follow-up drafts)
                                                   │
           Meeting transcripts ─► Learner ─► proposals ─(approve)─► profile/services
                                                   │
                         First engagement ends ─► Offer designer re-runs
```

**6 agents:** Interviewer, Offer designer, Researcher, Writer, Verifier, Learner.
**Deterministic (code):** Scout, demand index, Verifier pre-checks, Tracker, provider integrations.

**Model tiers** (OpenAI Responses API via `app/ai/client.py`; model IDs are settings, see CLAUDE.md → Models):
| Tier | Used by | Role in code | Setting (current model) |
|---|---|---|---|
| Judgment | Interviewer, Offer designer, Writer, Learner | `smart` | `OPENAI_MODEL_SMART` (`gpt-6-astra`) |
| Standard | Researcher, Verifier | `check` | `OPENAI_MODEL_CHECK` (`gpt-6.1-sol`) |
| Fast | Optional fallback skill tagging | `fast` | `OPENAI_MODEL_FAST` (`gpt-6-luna`) |

The Verifier runs on a different model than the Writer and with fresh context, so the writer never grades itself.

---

## 4. Domain entities

Conceptual entities and the attributes the pipeline relies on. Implement in whatever data layer the repo uses.

| Entity | Key attributes |
|---|---|
| **Freelancer** | name, segment, languages, location, preferences |
| **Profile** (versioned) | skills with evidence and proof strength, skill combinations, industries, exclusions, rate floor, availability, past employers, schools, voice samples, approval state |
| **Service** (versioned) | name, target buyer, problem, deliverable, skills used, rate range, demand index, proof strength, discovery terms, approval state |
| **Company** | job-board slug, name, domain, sector, how discovered, last fetched |
| **Lead** | source posting id (unique), company, title, department, location, remote flag, employment type, language, published date, description, posting URL, apply URL, tagged skills, status |
| **Brief** | lead, fit decision, matched service, hook (summary, URL, date), problem hypothesis, channel, warm connection, sources |
| **Draft** | lead, channel, language, round, subject (optional), body, verifier result, status |
| **Outreach event** | draft, event type (copied, sent, positive reply, not interested, no reply, meeting), timestamp |
| **Engagement** | freelancer, company, start, end, status |
| **Meeting** | engagement, transcript, consent confirmed, date |
| **Learning** | meeting or engagement, Learner output, proposal approval state |
| **Posting snapshot** | daily record of baseline-panel postings, used for the demand index |

---

## 5. Agents

### 5.1 Interviewer · Judgment tier
**Purpose:** Turn a resume into an evidence-backed profile through a short, engaging conversation.
**Trigger:** onboarding; optional re-interview after the first engagement ends.
**Inputs:** resume text, freelancer segment, preferred language.
**Tools:** none.

**Rules**
- Read the resume first. Ask **one question at a time**, each tied to a specific resume line.
- Priority: quantified outcomes → citable clients/employers → distinctive skill combinations → preferred work → exclusions → rate floor → availability → 2–3 writing samples for voice.
- Engaging: short turns, reflect back what's notable, never ask what the resume already answers, show progress ("about 4 questions left").
- Conduct the interview in the freelancer's chosen language (Dutch or English).
- Stop when all fields are covered or after ~15 questions; present a summary for approval.

**Output contract**
```json
{
  "skills": [{
    "name": "string",
    "taxonomy_id": "string|null",
    "evidence": [{"claim": "string", "metric": "string|null", "client": "string|null", "citable": true}],
    "proof_strength": "claimed|demonstrated|referenceable"
  }],
  "skill_combinations": [{"skills": ["string"], "evidence": "string"}],
  "industries": ["string"],
  "preferred_work": ["string"],
  "exclusions": ["string"],
  "rate_floor_eur_hour": "number|null",
  "availability_hours_week": "number|null",
  "past_employers": ["string"],
  "schools": ["string"],
  "languages": ["nl|en"],
  "voice_samples": ["string"]
}
```
**Acceptance:** every skill above `claimed` has ≥ 1 evidence item; nothing invented beyond resume and answers.

---

### 5.2 Offer designer · Judgment tier
**Purpose:** Turn the profile into 3–5 sellable services ranked by demand, framed as independent projects, plus the search terms that drive lead discovery.
**Trigger:** after profile approval; after the first engagement ends (with approved Learner proposals).
**Inputs:** approved profile; demand index inputs per skill and skill pair (§6.2).
**Tools:** none.

**Rules**
- Build services around **skill combinations**, not single skills.
- Each service: target buyer, problem, **concrete deliverable with an end point**, rate range.
- **Wet DBA check:** reject or reframe any service that is open-ended, hours-based, or describes working under a client's direction inside their team. Prefer fixed scope, own methods, own tools, multiple clients possible.
- Read the demand index from code; explain it, don't compute it.
- Generate Dutch and English discovery terms per service.

**Output contract**
```json
{
  "services": [{
    "id": "string",
    "name": "string",
    "target_buyer": "string",
    "problem": "string",
    "deliverable": "string",
    "duration_weeks": "number",
    "skills_used": ["string"],
    "rate_range_eur": {"min": 0, "max": 0},
    "demand_index": 0.0,
    "proof_strength": "claimed|demonstrated|referenceable",
    "independence_rationale": "string",
    "rationale": "string",
    "discovery_terms": {"nl": ["string"], "en": ["string"]}
  }]
}
```
**Acceptance:** 3–5 services; each uses ≥ 1 skill with `demonstrated` or `referenceable` proof; each has a bounded deliverable and an independence rationale.

---

### 5.3 Researcher · Standard tier
**Purpose:** For each lead, decide fit and build the brief: a recent hook, a problem hypothesis, the matched service and the best channel.
**Trigger:** each new lead passing Scout filters.
**Inputs:** lead, approved services, profile (past employers, schools, industries).
**Tools:** `search(query, freshness_days)`, `fetch_page(url)` (§7).

**Rules**
1. Find company activity from the **last 30 days**: news, company blog, launches, funding, leadership changes. Prefer company and news sources; results about individuals are pointers only, never stored as profiles.
2. Pick **one hook** with source URL and publish date.
3. Form a problem hypothesis linking posting + hook to one service.
4. No service fits → `fit: false` with reason; lead is dropped.
5. Channel:
   - `warm_intro` if the company overlaps with past employers, schools or known clients.
   - `apply_interim` (default): note for the posting's apply form, offering to deliver a scoped project now.
   - `direct_note` only when the company has published a role address (e.g., jobs@, info@) or the freelancer supplies a contact.

**Output contract**
```json
{
  "fit": true,
  "fit_reason": "string",
  "service_id": "string|null",
  "hook": {"summary": "string", "source_url": "string", "published_at": "YYYY-MM-DD"},
  "problem_hypothesis": "string",
  "channel": "warm_intro|apply_interim|direct_note",
  "warm_connection": "string|null",
  "draft_language": "nl|en",
  "sources": [{"url": "string", "title": "string", "published_at": "YYYY-MM-DD|null"}]
}
```
`draft_language` follows the posting's language unless the freelancer overrides.
**Acceptance:** every factual statement traces to `sources`; hook dated within 30 days.

---

### 5.4 Writer · Judgment tier
**Purpose:** Write a short, ready-to-paste message that grabs attention and proposes a real solution.
**Trigger:** each brief with `fit: true`; again on Verifier notes (max 2 revisions); follow-up drafts when the Tracker requests them.
**Inputs:** brief, matched service, profile evidence, voice samples, channel, language, revision notes.
**Tools:** none.

**Rules**
- First sentence: the hook (specific, recent, attributed).
- Solution in **≤ 2 sentences**, specific to this company, framed as a scoped project with a deliverable.
- One proof point, only from profile evidence.
- One low-friction ask.
- ≤ 120 words, in the freelancer's voice, in `draft_language`. No flattery, no filler.
- Never use employment framing ("join your team", "full-time", "available 40 hours/week").
- Channel formats:
  - `apply_interim`: note for the application form.
  - `warm_intro`: message to the mutual contact + a forwardable blurb.
  - `direct_note`: message with subject line (≤ 7 words), plus an opt-out line.

**Output contract**
```json
{"channel": "string", "language": "nl|en", "subject": "string|null", "body": "string", "forwardable_blurb": "string|null", "claims_used": ["string"], "hook_source_url": "string"}
```

---

### 5.5 Verifier · Standard tier (with code pre-checks)
**Purpose:** Stop invented claims, generic pitches and risky framing before the freelancer sees a draft.
**Trigger:** every Writer draft.

**Step 1: code pre-checks (no model call; fail fast)**
- Hook source URL resolves.
- Source publish date within 30 days.
- Key hook facts (names, numbers) appear on the fetched page.
- Word count ≤ 120; subject ≤ 7 words where required.
- No unfilled placeholders; no banned phrases (configurable list, incl. employment-framing phrases in NL and EN).
- Every `claims_used` item exists in profile evidence.
- `direct_note` includes an opt-out line.

Failures go back to the Writer with the failing checks listed.

**Step 2: model review**
- Company claims supported by the brief's sources; paraphrases accurate.
- Solution specific to this company (if it would make sense for any company, fail).
- First line earns attention.
- Reads as independent project work, not disguised employment.
- Tone matches voice samples; language is natural Dutch/English.

**Output contract**
```json
{"pass": true, "issues": [{"severity": "blocker|major|minor", "check": "string", "detail": "string"}], "revision_notes": "string|null"}
```
**Loop:** fail → Writer revises (max 2 rounds). Still failing → draft marked `needs_human` with issues shown.

---

### 5.6 Learner · Judgment tier
**Purpose:** Learn from client meetings and outreach outcomes: sentiment, what's working, what isn't, and skills being acquired.
**Trigger:** each uploaded transcript; engagement end (summary + triggers Offer designer re-run).
**Inputs:** transcript (only if consent confirmed), engagement context, profile, outreach stats (code).
**Tools:** none.

**Rules**
- Sentiment per meeting with evidence. Weigh concrete signals (scope changes, follow-up requests, delays) over polite tone.
- What's working / not working, each with evidence.
- Propose new skills or proof points only with evidence from the engagement.
- Output **proposals only**; the freelancer approves.

**Output contract**
```json
{
  "sentiment": {"score": 0, "trend": "up|flat|down", "evidence": ["string"]},
  "working": [{"point": "string", "evidence": "string"}],
  "not_working": [{"point": "string", "evidence": "string"}],
  "risks": ["string"],
  "skill_proposals": [{"skill": "string", "evidence": "string", "proof_strength": "claimed|demonstrated|referenceable"}],
  "service_proposals": [{"change": "string", "rationale": "string"}]
}
```
Sentiment score range: −2 to +2.

---

## 6. Deterministic components

### 6.1 Scout (Recruitee ingestion)
1. **Discovery:** for each discovery term, search `site:recruitee.com "<term>" Amsterdam` (NL and EN terms) via the search provider. Extract company slugs from `{slug}.recruitee.com` URLs.
2. **Baseline panel:** separately maintain a fixed panel of Amsterdam/NL Recruitee companies (target 500–1,000), chosen independently of any freelancer's skills, for the demand index.
3. **Fetch:** `GET https://{slug}.recruitee.com/api/offers/` (public, no auth) **(verify endpoint and field names against a live response)**. Be polite: ≤ 1 request/second, back off on errors.
4. **Normalize** to the Lead entity: id, title, department, location/city, remote flag, employment type, published date, description, posting URL, apply URL.
5. **Dedupe** on source posting id.
6. **Filter:** published ≤ 30 days; Amsterdam/NL or remote-compatible; exclude freelancer exclusions.
7. **Language detection** on title + description (nl/en).
8. **Skill tagging:** keyword match against ESCO skills (EU taxonomy, available in Dutch and English) plus the freelancer's own skill names and synonyms. Optional fast-tier model fallback only for postings with zero matches.
9. Schedule: daily. Snapshot the baseline panel daily.

Design job sources behind an interface so Lever and Personio can be added later as additional implementations.

### 6.2 Demand index
A **hiring demand index** (0–1), not a claim about total market demand.
| Component | Weight (config) | Input |
|---|---|---|
| Posting signal | 0.5 | From baseline panel snapshots: 90-day frequency per skill and skill pair, 30-day trend, pair co-occurrence premium, boost for contract/freelance/interim employment types |
| Sector signal | 0.2 | CBS open data: vacancies and flex work by sector (**verify** current tables) |
| Revealed demand | 0.3 | Freelancer's own outcomes: positive-reply rate per service. Starts at 0 weight; grows with sample size until it dominates |

Service-level index = max over the service's skill pairs × proof factor (claimed 0.5, demonstrated 0.8, referenceable 1.0). Store all inputs so the Offer designer can explain the number. Cold start: no posting history at launch; index uses the current snapshot until history builds.

### 6.3 Tracker (manual, in app)
Lead statuses:
`discovered → filtered → researched → (dropped | drafted) → verified → ready → copied → sent → positive_reply | not_interested | no_reply → meeting → won | lost`
- Freelancer updates statuses with one-click actions on each lead.
- "Sent" starts a reminder: after 5 business days (configurable), ask "Did they reply?" and offer a pre-written follow-up (Writer → Verifier). Max one follow-up.
- `not_interested` stops all drafts for that company.
- Log every status change with a timestamp; these are the Learner's outreach data.
- Show the freelancer their own funnel and reply rates (incentive to log outcomes).

### 6.4 Verifier pre-checks
See §5.5 step 1. Implement as plain functions with unit tests.

### 6.5 Compliance guidance (shown in UI)
- `direct_note`: if the recipient address contains a person's name, warn that Dutch rules on unsolicited email and GDPR apply, and suggest a published role address instead. Opt-out line required.
- `apply_interim` and `warm_intro`: no warning needed.
- Meeting upload: require explicit confirmation that all participants consented to recording.
- Service and draft framing: Wet DBA guidance (§2.4).
- Footer: guidance is not legal advice.

---

## 7. Provider interfaces

```
SearchProvider
  search(query, freshness_days?, count?, locale?) -> [ {url, title, snippet, published_at?} ]

PageFetcher
  fetch(url) -> {text, published_at?, status}

JobSource
  discover(terms, location) -> [company_slug]
  fetch_postings(company_slug) -> [Lead]
```
- **SearchProvider default:** Oxylabs search results API (**verify** endpoint, auth and parameters for locale `nl`, Google domain `google.nl`, and recency filtering).
- **PageFetcher:** use the repo's existing HTTP client; Oxylabs can serve as fallback for pages that block plain requests.
- **JobSource default:** Recruitee.
- Keep provider credentials in the repo's existing config/secrets mechanism.

---

## 8. UI surfaces needed

Adapt to the repo's existing UI framework and patterns.
1. **Interview:** chat view with progress indicator; resume upload.
2. **Profile and services review:** edit and approve.
3. **Lead queue:** leads with brief, matched service, hook and channel; filter by status.
4. **Draft view:** draft text, language toggle (regenerate in the other language), **Copy** button, link to the posting and apply form, Verifier notes, compliance guidance, regenerate.
5. **Status actions:** one-click buttons per lead; reminder inbox.
6. **Insights:** funnel and reply rates by service, channel and hook type; Learner proposals for approval.

---

## 9. Milestones

| # | Milestone | Done when |
|---|---|---|
| 1 | Repo mapping + plan | Implementation plan approved |
| 2 | Scout + baseline panel | From 5 discovery terms: ≥ 50 deduped, filtered Recruitee leads with language and skill tags; daily snapshot running |
| 3 | Interviewer + profile approval | A real resume yields an approved profile in ≤ 20 minutes |
| 4 | Demand index + Offer designer | Approved profile yields 3–5 Wet DBA-safe services with explained demand index and NL/EN discovery terms |
| 5 | Researcher | For 20 leads: dated, sourced hooks; non-fits dropped with reasons |
| 6 | Writer + Verifier | ≥ 70% of drafts pass within 2 rounds; zero unsupported claims in a manual audit of 20 (NL and EN) |
| 7 | Draft view + Tracker | Freelancer can copy, send externally, log outcomes; reminders and follow-up drafts work |
| 8 | Learner + insights | Transcript upload produces evidence-backed output; proposals require approval; funnel view live |

---

## 10. Metrics (log from day one)
- Funnel per service: discovered → filtered → fit → drafted → copied → sent → replied.
- Positive-reply rate by channel, service, hook type and language.
- Logging rate: share of copied drafts with an outcome recorded.
- Edit rate: freelancer edits before copying (quality proxy, if edits happen in-app).
- Cost per ready draft (model + search calls).

---

## 11. Open questions
1. Freelancer segment: tech/data (default) vs broader business professionals. The latter would add public administration via broker platforms and TenderNed.
2. Source of the sector flex-hiring figures and whether "flexible hiring" there means freelancers or all flex contracts.
3. Wet DBA framing: review service templates and banned phrases with Dutch counsel; track the planned replacement law (Wet VBAR).
4. Oxylabs: confirm terms of use for this purpose, cost per query and recency filtering support.
5. Rate benchmarks for Dutch freelancers in the MVP segment.
