// Shapes from docs/contract.md. Keep in sync with fixtures/.

export type Check = { name: string; passed: boolean; note?: string }

export type State = {
  profile: { name: string; headline: string }
  today: { new_leads: number; drafts_pending: number; deadlines_this_week: number; replies_unread: number }
  last_checkin_at: string
  mode: string
}

export type Persona = {
  name: string
  role: string
  years: number | null
  location: string
  skills: string[]
  wants: string
  avoids: string
  hours_per_week: number | null
  income_goal_monthly: number | null
  sources: string[]
}

/** Background action status (docs/contract.md → AI actions). */
export type Job = {
  status: 'idle' | 'running' | 'done' | 'error'
  step: string | null
  message: string | null
  error: string | null
  result: Record<string, unknown> | null
  updated_at: string | null
}

export type Fit = {
  fits: { name: string; level: 'high' | 'medium' | 'low'; reason: string; sources: string[] }[]
  rate: {
    // null until the profile has been built (real mode starts empty)
    floor: number | null
    ask: number | null
    low: number | null
    high: number | null
    currency: string
    unit: string
    reasons: { text: string; source: string }[]
  }
  goal_checks: Check[]
}

export type Offer = { id: string; name: string; pitch: string; price: string; evidence: string; fit: string }

export type Lead = {
  id: string
  nickname: string
  title: string
  company: string | null
  source: string
  url: string
  fetched: boolean
  rate_text: string | null
  score: number | null
  why: string
  status: string
  found_at: string
  verdict_note?: string
}

export type Draft = {
  id: string
  lead_id: string
  nickname: string
  channel: 'email' | 'apply_link'
  to: string
  subject: string
  body: string
  verifier: { passed: boolean; checks: Check[] }
  status: 'pending' | 'approved' | 'sent' | 'rejected'
  created_at: string
  approved_at?: string
  sent_at?: string
  reject_note?: string
}

export type Pipeline = {
  columns: { name: string; items: { nickname: string; title?: string; note: string }[] }[]
  deadlines: { nickname: string; what: string; due: string }[]
}

export type Metric = {
  name: string
  value: number
  low: number | null
  high: number | null
  n: number | null
  direction: 'up' | 'down'
  display?: string
}

export type Metrics = {
  week: string
  headline: Metric[]
  channels: { name: string; sent: number; replies: number; value: number; low: number; high: number; n: number }[]
  guardrails: Record<string, number>
  learning: { id: string; text: string; status: string }[]
}

export type ChatEvent =
  | { type: 'text'; delta: string }
  | { type: 'tool'; name: string; status: 'running' | 'done' }
  | { type: 'done' }
  | { type: 'error'; message: string }

/* Spec v2: Interviewer and versioned profile (see docs/contract.md, proposed) */
export type InterviewTurn = {
  question: string
  resume_line: string | null
  reflection: string
  topic: string
  answer: string | null
}

export type InterviewState = {
  status: 'not_started' | 'active' | 'complete' | 'extracted'
  language?: 'nl' | 'en'
  turns: InterviewTurn[]
  progress: { asked: number; left: number; max: number } | null
  transcript_ref?: string | null
  job: Job
}

export type ProofStrength = 'claimed' | 'demonstrated' | 'referenceable'

export type ProfileData = {
  skills: {
    name: string
    taxonomy_id: string | null
    evidence: { claim: string; metric: string | null; client: string | null; citable: boolean }[]
    proof_strength: ProofStrength
  }[]
  skill_combinations: { skills: string[]; evidence: string }[]
  industries: string[]
  preferred_work: string[]
  exclusions: string[]
  rate_floor_eur_hour: number | null
  availability_hours_week: number | null
  past_employers: string[]
  schools: string[]
  languages: ('nl' | 'en')[]
  voice_samples: string[]
}

export type ProfileVersion = {
  version: number
  status: 'draft' | 'approved' | 'superseded'
  author: 'agent' | 'user'
  data: ProfileData
  checks: Check[]
  provenance: { model: string | null; prompt_version: string | null; input_ref: unknown; ts: string } | null
  created_at: string
  approved_at: string | null
}

export type ProfileDoc = { latest: ProfileVersion | null; approved: ProfileVersion | null }
