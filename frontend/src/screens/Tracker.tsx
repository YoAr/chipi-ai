import { useState } from 'react'
import { api, useApi, useJob } from '../api'
import type { Tab } from '../App'
import type { Funnel, Lead, Reminder } from '../types'
import { JobStatus, Loading, Page } from '../ui'

const LABEL: Record<string, string> = {
  sent: 'I sent it', positive_reply: 'Positive reply', not_interested: 'Not interested', no_reply: 'No reply',
  meeting: 'Meeting booked', won: 'Won', lost: 'Lost',
}
const ACTIONS: Record<string, string[]> = {
  drafted: ['sent'], verified: ['sent'], ready: ['sent'], copied: ['sent'],
  sent: ['positive_reply', 'not_interested', 'no_reply'], no_reply: ['positive_reply', 'not_interested', 'sent'],
  positive_reply: ['meeting', 'won', 'lost'], meeting: ['won', 'lost'],
}
const COLUMNS = [
  ['To send', ['ready', 'copied', 'drafted']],
  ['Waiting for a reply', ['sent', 'no_reply']],
  ['In conversation', ['positive_reply', 'meeting']],
  ['Done', ['won', 'lost', 'not_interested']],
] as const
const STAGE: Record<string, string> = {
  discovered: 'Found', filtered: 'Filtered', researched: 'Researched', drafted: 'Drafted', copied: 'Copied',
  sent: 'Sent', replied: 'Replied', positive_reply: 'Positive', meeting: 'Meeting', won: 'Won',
}
const pct = (x: number | null) => (x == null ? '—' : `${Math.round(x * 100)}%`)

/** One-click outcome buttons; the server only accepts the spec's transitions. */
export function StatusButtons({ lead, onChange }: { lead: Pick<Lead, 'id' | 'status'>; onChange: () => void }) {
  const [error, setError] = useState<string | null>(null)
  const actions = ACTIONS[lead.status] ?? []
  async function move(status: string) {
    setError(null)
    try { await api.post(`/api/leads/${lead.id}/status`, { status }); onChange() }
    catch (e) { setError((e as Error).message) }
  }
  if (actions.length === 0) return null
  return (
    <>
      <div className="row-actions" style={{ alignSelf: 'flex-start' }}>
        {actions.map((a) => <button key={a} className="btn" onClick={() => move(a)}>{LABEL[a]}</button>)}
      </div>
      {error && <p className="small error">{error}</p>}
    </>
  )
}

function FollowupButton({ leadId, onDone }: { leadId: string; onDone: () => void }) {
  const job = useJob(`/api/leads/${leadId}/followup`, `/api/leads/${leadId}/draft/status`, onDone, false)
  return (
    <>
      <button className="btn btn-primary" disabled={job.running} onClick={() => job.start()}>
        {job.running ? 'Writing and checking…' : 'Write a follow-up'}
      </button>
      <JobStatus job={job.job} startError={job.startError} />
    </>
  )
}

/** "Did they reply?" inbox (spec v2 §6.3, §8.5), shown on Today. */
export function ReminderInbox({ go }: { go: (t: Tab) => void }) {
  const data = useApi<{ reminders: Reminder[] }>('/api/reminders')
  const items = data.data?.reminders ?? []
  if (items.length === 0) return null
  return (
    <div className="card">
      <h2>Did they reply?</h2>
      <p className="muted small">You marked these sent at least 5 working days ago. Logging the outcome is how Chipi learns what works.</p>
      <div className="list">
        {items.map((r) => (
          <div key={r.id} className="row row-lead">
            <div className="grow">
              <b>{r.lead.title}</b>
              <p className="muted small">{r.lead.company} · sent, reminder due {r.due_at.slice(0, 10)}</p>
              <StatusButtons lead={r.lead} onChange={() => data.reload()} />
            </div>
            {r.followup_allowed && !r.followup && <FollowupButton leadId={r.lead.id} onDone={() => go('drafts')} />}
            {r.followup && <button className="btn" onClick={() => go('drafts')}>Follow-up drafted</button>}
          </div>
        ))}
      </div>
    </div>
  )
}

/** Spec v2 Tracker: funnel, reply rates, and every lead after the draft stage with one-click outcomes. */
export function TrackerView({ go }: { go: (t: Tab) => void }) {
  const leads = useApi<{ leads: Lead[] }>('/api/leads')
  const funnel = useApi<Funnel>('/api/funnel')
  const reload = () => { leads.reload(); funnel.reload() }
  if (!leads.data || !funnel.data) return <Loading error={leads.error ?? funnel.error} />
  const f = funnel.data

  return (
    <Page title="Pipeline" lede="Every lead you’ve drafted, sent or heard back from. One click logs what happened.">
      <div className="card">
        <h2>Your funnel</h2>
        <div className="funnel">
          {f.stages.map((s) => (
            <div key={s.stage} className="funnel-step"><b>{s.count}</b><span className="muted small">{STAGE[s.stage] ?? s.stage}</span></div>
          ))}
        </div>
        <p className="small">
          Positive reply rate <b>{pct(f.rates.positive_reply_rate)}</b> · reply rate <b>{pct(f.rates.reply_rate)}</b> ·
          outcomes logged for <b>{pct(f.rates.logging_rate)}</b> of copied drafts
          {f.rates.sent < 10 && <span className="muted"> · only {f.rates.sent} sent so far, so rates will move a lot</span>}
        </p>
      </div>
      <ReminderInbox go={go} />
      <div className="board">
        {COLUMNS.map(([name, statuses]) => {
          const items = leads.data!.leads.filter((l) => (statuses as readonly string[]).includes(l.status))
          return (
            <div key={name} className="col">
              <h3>{name} <span className="muted">{items.length}</span></h3>
              {items.map((l) => (
                <div key={l.id} className="tile">
                  <b>{l.title}</b>
                  <p className="muted small">{l.company} · {l.status.replace('_', ' ')}</p>
                  <StatusButtons lead={l} onChange={reload} />
                </div>
              ))}
            </div>
          )
        })}
      </div>
    </Page>
  )
}
