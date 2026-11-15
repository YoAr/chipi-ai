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
  years: number
  location: string
  skills: string[]
  wants: string
  avoids: string
  hours_per_week: number | null
  income_goal_monthly: number | null
  sources: string[]
}

export type Fit = {
  fits: { name: string; level: 'high' | 'medium' | 'low'; reason: string; sources: string[] }[]
  rate: {
    floor: number
    ask: number
    low: number
    high: number
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
  status: string
  created_at: string
}

export type Pipeline = {
  columns: { name: string; items: { nickname: string; note: string }[] }[]
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
