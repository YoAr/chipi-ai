import { useState, type FormEvent } from 'react'
import { api, useApi, useJob } from '../api'
import type { Tab } from '../App'
import { useIsReal } from '../mode'
import type { Lead } from '../types'
import { Empty, JobStatus, Loading, Page, sourceLabel } from '../ui'

const VERDICTS = [
  ['useful', 'Useful'],
  ['maybe', 'Maybe'],
  ['skip', 'Skip'],
] as const

const DRAFTABLE = new Set(['new', 'useful', 'maybe'])

function LeadRow({ lead, real, onVerdict, go }: { lead: Lead; real: boolean; onVerdict: (v: string) => void; go: (t: Tab) => void }) {
  // One draft job per lead; no status request until the button is pressed.
  const draft = useJob(`/api/leads/${lead.id}/draft`, `/api/leads/${lead.id}/draft/status`, () => go('drafts'), false)

  return (
    <div className="row row-lead">
      <span className="score" title="Match score">{lead.score ?? '—'}</span>
      <div className="grow">
        {/^https?:\/\//i.test(lead.url) ? (
          <a href={lead.url} target="_blank" rel="noreferrer"><b>{lead.title}</b></a>
        ) : (
          <b>{lead.title}</b>
        )}
        <p className="muted small">
          {sourceLabel(lead.source)} · {lead.rate_text ?? 'rate unknown'}
          {!lead.fetched && ' · not fetched yet'}
        </p>
        {lead.why && <p className="small">Why: {lead.why}</p>}
        <JobStatus job={draft.job} startError={draft.startError} />
      </div>
      <div className="row-actions">
        <div className="seg" role="group" aria-label={`Verdict for ${lead.title}`}>
          {VERDICTS.map(([v, label]) => (
            <button key={v} className={lead.status === v ? 'seg-btn seg-on' : 'seg-btn'} aria-pressed={lead.status === v} onClick={() => onVerdict(v)}>
              {label}
            </button>
          ))}
        </div>
        {real && DRAFTABLE.has(lead.status) && (
          <button className="btn" disabled={draft.running} onClick={() => draft.start()}>
            {draft.running ? 'Writing…' : 'Draft outreach'}
          </button>
        )}
      </div>
    </div>
  )
}

export function Leads({ go }: { go: (t: Tab) => void }) {
  const real = useIsReal()
  const { data, error, reload } = useApi<{ leads: Lead[] }>('/api/leads')
  const search = useJob('/api/search', '/api/search/status', () => reload(), real)
  const [url, setUrl] = useState('')
  const [msg, setMsg] = useState<string | null>(null)

  async function verdict(id: string, v: string) {
    await api.post(`/api/leads/${id}/verdict`, { verdict: v })
    reload()
  }

  async function addSeed(e: FormEvent) {
    e.preventDefault()
    try {
      await api.post('/api/seeds', { url })
      setUrl('')
      setMsg('Added to your leads.')
      reload()
    } catch (err) {
      setMsg((err as Error).message)
    }
  }

  return (
    <Page title="Leads" lede="Ranked by how well they fit you. Your verdicts teach the next search.">
      {real && (
        <div className="callout">
          <span>Search RemoteOK, Remotive and Hacker News for work that fits you.</span>
          <button className="btn btn-primary" disabled={search.running} onClick={() => search.start()}>
            {search.running ? 'Searching…' : 'Find leads'}
          </button>
        </div>
      )}
      <JobStatus job={search.job} startError={search.startError} />

      <form className="inline-form" onSubmit={addSeed}>
        <label htmlFor="seed" className="sr-only">Job post link</label>
        <input id="seed" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Found a job post? Paste the link" />
        <button className="btn">Add</button>
      </form>
      {msg && <p className="muted small">{msg}</p>}

      {!data ? (
        <Loading error={error} />
      ) : data.leads.length === 0 ? (
        <Empty title="No leads yet">
          {real ? 'Press Find leads, or paste a job post you found.' : 'Paste a job post you found.'}
        </Empty>
      ) : (
        <div className="list">
          {data.leads.map((l) => (
            <LeadRow key={l.id} lead={l} real={real} go={go} onVerdict={(v) => verdict(l.id, v)} />
          ))}
        </div>
      )}
    </Page>
  )
}
