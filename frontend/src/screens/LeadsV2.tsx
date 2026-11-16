import { useState } from 'react'
import { useApi, useJob } from '../api'
import type { Tab } from '../App'
import type { Lead, ServicesDoc } from '../types'
import { Empty, JobStatus, Loading, Page } from '../ui'

const FILTERS = [
  ['researched', 'Researched'],
  ['filtered', 'To research'],
  ['dropped', 'Dropped'],
  ['', 'All'],
] as const

const CHANNEL: Record<string, string> = {
  apply_interim: 'Note in the application form',
  warm_intro: 'Warm intro',
  direct_note: 'Direct note',
}

const date = (iso?: string | null) => (iso ? iso.slice(0, 10) : '')

function LeadCard({ lead, services }: { lead: Lead; services: Record<string, string> }) {
  const b = lead.brief
  return (
    <div className="card lead">
      <div className="interview-head">
        <div>
          <a href={lead.url} target="_blank" rel="noreferrer"><b>{lead.title}</b></a>
          <p className="muted small">
            {lead.company} · {lead.location ?? 'location unknown'}{lead.remote ? ' · remote' : ''} ·{' '}
            {lead.language === 'nl' ? 'Dutch' : 'English'} · posted {date(lead.published_at)}
          </p>
        </div>
        <span className={lead.status === 'researched' ? 'badge badge-ok' : 'badge'}>{lead.status}</span>
      </div>
      {lead.skills && lead.skills.length > 0 && (
        <div className="chips">{lead.skills.map((s) => <span key={s} className="chip">{s}</span>)}</div>
      )}
      {b && b.fit && b.hook && (
        <div className="brief">
          <p className="small">
            <b>Hook ({b.hook.type}, {b.hook.published_at}):</b> {b.hook.summary}{' '}
            <a href={b.hook.source_url} target="_blank" rel="noreferrer" className="muted">source</a>
          </p>
          <p className="small"><b>Service:</b> {services[b.service_id ?? ''] ?? b.service_id}</p>
          <p className="small"><b>Likely problem:</b> {b.problem_hypothesis}</p>
          <p className="small">
            <b>Channel:</b> {CHANNEL[b.channel] ?? b.channel}
            {b.warm_connection && <> via {b.warm_connection}</>} · draft in {b.draft_language === 'nl' ? 'Dutch' : 'English'}
          </p>
        </div>
      )}
      {b && !b.fit && <p className="small muted"><b>Dropped:</b> {b.fit_reason}</p>}
      {lead.research_error && !b && <p className="small error">Research didn’t finish: {lead.research_error}</p>}
    </div>
  )
}

/** Spec v2 lead queue: Scout leads, Researcher briefs, filter by status. */
export function LeadsV2({ go }: { go: (t: Tab) => void }) {
  const [filter, setFilter] = useState<string>('researched')
  const leads = useApi<{ leads: Lead[] }>(filter ? `/api/leads?status=${filter}` : '/api/leads')
  const services = useApi<ServicesDoc>('/api/services')
  const scout = useJob('/api/scout', '/api/scout/status', () => leads.reload())
  const research = useJob('/api/research', '/api/research/status', () => leads.reload())
  const names = Object.fromEntries((services.data?.approved?.data.services ?? []).map((s) => [s.id, s.name]))
  const approved = !!services.data?.approved

  return (
    <Page title="Leads" lede="Dutch companies hiring for what you sell, with a recent, sourced reason to reach out now.">
      {services.data && !approved ? (
        <Empty title="Approve your services first"
               action={<button className="btn btn-primary" onClick={() => go('offers')}>Go to services</button>}>
          Chipi searches with your services’ Dutch and English terms and matches each lead to one service.
        </Empty>
      ) : (
        <div className="callout">
          <span>Find new Recruitee postings, then research them: a dated hook, the matching service and the channel.</span>
          <div className="row-actions">
            <button className="btn" disabled={scout.running} onClick={() => scout.start()}>
              {scout.running ? 'Searching…' : 'Find leads'}
            </button>
            <button className="btn btn-primary" disabled={research.running} onClick={() => research.start({ limit: 10 })}>
              {research.running ? 'Researching…' : 'Research next 10'}
            </button>
          </div>
        </div>
      )}
      <JobStatus job={scout.job} startError={scout.startError} />
      <JobStatus job={research.job} startError={research.startError} />

      <div className="seg" role="group" aria-label="Filter leads by status">
        {FILTERS.map(([v, label]) => (
          <button key={v} className={filter === v ? 'seg-btn seg-on' : 'seg-btn'} aria-pressed={filter === v}
                  onClick={() => setFilter(v)}>{label}</button>
        ))}
      </div>

      {!leads.data ? (
        <Loading error={leads.error} />
      ) : leads.data.leads.length === 0 ? (
        <Empty title="Nothing here yet">
          {filter === 'researched' ? 'Research some leads to see briefs here.' : 'Press Find leads to search Recruitee boards.'}
        </Empty>
      ) : (
        <div className="list">
          {leads.data.leads.map((l) => <LeadCard key={l.id} lead={l} services={names} />)}
        </div>
      )}
    </Page>
  )
}
