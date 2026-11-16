import { useState } from 'react'
import { api, useApi, useJob } from '../api'
import type { Tab } from '../App'
import type { Draft, Lead } from '../types'
import { Checks, Empty, JobStatus, Loading, Page } from '../ui'

const CHANNEL: Record<string, string> = {
  apply_interim: 'Paste into the application form',
  warm_intro: 'Send to your mutual contact',
  direct_note: 'Email to a published role address',
}
const FILTERS = [['open', 'To copy'], ['copied', 'Copied']] as const

function DraftCard({ draft, lead, onChange }: { draft: Draft; lead?: Lead; onChange: () => void }) {
  const [body, setBody] = useState(draft.final_text ?? draft.body)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const other = draft.language === 'nl' ? 'en' : 'nl'
  const rewrite = useJob(`/api/leads/${draft.lead_id}/draft`, `/api/leads/${draft.lead_id}/draft/status`, onChange, false)
  const v = draft.verifier

  async function copy() {
    setBusy(true)
    setMsg(null)
    const text = draft.subject ? `${draft.subject}\n\n${body}` : body
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      setMsg('Couldn’t reach the clipboard. Select the text and copy it yourself.')
    }
    try {
      await api.post(`/api/drafts/${draft.id}/copied`, { final_text: body })
      setMsg('Copied. Paste it where it goes and send it yourself; Chipi never sends anything.')
      onChange()
    } catch (e) {
      setMsg((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card draft">
      <div className="interview-head">
        <div>
          <b>{lead?.title ?? draft.nickname}</b>
          <p className="muted small">
            {lead?.company} · {CHANNEL[draft.channel] ?? draft.channel} · {draft.language === 'nl' ? 'Dutch' : 'English'}
          </p>
        </div>
        <span className={draft.status === 'ready' ? 'badge badge-ok' : 'badge'}>
          {draft.status === 'ready' ? 'Passed every check' : draft.status === 'needs_human' ? 'Needs your review' : 'Copied'}
        </span>
      </div>
      {draft.subject && <p><b>Subject:</b> {draft.subject}</p>}
      <label htmlFor={`body-${draft.id}`} className="sr-only">Message</label>
      <textarea id={`body-${draft.id}`} rows={8} value={body} onChange={(e) => setBody(e.target.value)} />
      <p className="muted small">{body.trim().split(/\s+/).filter(Boolean).length} words</p>
      {draft.forwardable_blurb && (
        <div className="brief"><p className="small"><b>Blurb they can forward:</b> {draft.forwardable_blurb}</p></div>
      )}
      <div className="row-actions" style={{ alignSelf: 'flex-start' }}>
        <button className="btn btn-primary" disabled={busy} onClick={copy}>
          {draft.status === 'copied' ? 'Copy again' : 'Copy'}
        </button>
        {lead && <a className="btn" href={lead.apply_url ?? lead.url} target="_blank" rel="noreferrer">Open the posting</a>}
        <button className="btn btn-quiet" disabled={rewrite.running} onClick={() => rewrite.start({ language: other })}>
          {rewrite.running ? 'Rewriting…' : `Rewrite in ${other === 'nl' ? 'Dutch' : 'English'}`}
        </button>
        <button className="btn btn-quiet" disabled={rewrite.running} onClick={() => rewrite.start({ language: draft.language })}>
          Regenerate
        </button>
      </div>
      {msg && <p className="small" role="status">{msg}</p>}
      <JobStatus job={rewrite.job} startError={rewrite.startError} />
      <details>
        <summary className="small">Checked before you saw it{v.rounds ? ` · ${v.rounds} round${v.rounds > 1 ? 's' : ''}` : ''}</summary>
        <Checks checks={v.checks.map((c) => ({ ...c, note: c.note ?? undefined }))} />
      </details>
      {v.guidance && v.guidance.length > 0 && (
        <ul className="muted small guidance">{v.guidance.map((g) => <li key={g}>{g}</li>)}</ul>
      )}
    </div>
  )
}

/** Spec v2 draft view: copy to send yourself, language toggle, verifier notes, guidance. */
export function DraftsV2({ go }: { go: (t: Tab) => void }) {
  const [filter, setFilter] = useState<'open' | 'copied'>('open')
  const drafts = useApi<{ drafts: Draft[] }>('/api/drafts')
  const leads = useApi<{ leads: Lead[] }>('/api/leads')
  const batch = useJob('/api/drafts/write', '/api/drafts/write/status', () => drafts.reload())
  if (!drafts.data) return <Loading error={drafts.error} />
  const byLead = Object.fromEntries((leads.data?.leads ?? []).map((l) => [l.id, l]))
  const shown = drafts.data.drafts.filter((d) =>
    filter === 'open' ? d.status === 'ready' || d.status === 'needs_human' : d.status === 'copied')

  return (
    <Page title="Drafts" lede="Short, checked messages you copy and send yourself. Chipi never sends anything.">
      <div className="callout">
        <span>Write drafts for every researched lead that doesn’t have one yet.</span>
        <button className="btn btn-primary" disabled={batch.running} onClick={() => batch.start({ limit: 10 })}>
          {batch.running ? 'Writing…' : 'Draft researched leads'}
        </button>
      </div>
      <JobStatus job={batch.job} startError={batch.startError} />
      <div className="seg" role="group" aria-label="Filter drafts">
        {FILTERS.map(([v, label]) => (
          <button key={v} className={filter === v ? 'seg-btn seg-on' : 'seg-btn'} aria-pressed={filter === v}
                  onClick={() => setFilter(v)}>{label}</button>
        ))}
      </div>
      {shown.length === 0 ? (
        <Empty title={filter === 'open' ? 'No drafts to copy' : 'Nothing copied yet'}
               action={<button className="btn" onClick={() => go('leads')}>Go to leads</button>}>
          Research leads first, then write drafts for the ones that fit.
        </Empty>
      ) : (
        shown.map((d) => <DraftCard key={d.id} draft={d} lead={byLead[d.lead_id]} onChange={() => drafts.reload()} />)
      )}
      <p className="muted small">Compliance notes are guidance only, not legal advice.</p>
    </Page>
  )
}
