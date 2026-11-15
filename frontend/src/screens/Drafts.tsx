import { useState } from 'react'
import { api, useApi } from '../api'
import type { Tab } from '../App'
import { useIsReal } from '../mode'
import type { Draft } from '../types'
import { Checks, Empty, Loading, Page } from '../ui'

function DraftCard({ draft, real, onDone }: { draft: Draft; real: boolean; onDone: (note: string) => void }) {
  const [body, setBody] = useState(draft.body)
  const [rejecting, setRejecting] = useState(false)
  const [note, setNote] = useState('')
  const edited = body !== draft.body

  // Real mode only approves: sending isn't switched on yet, so never say "send".
  const approveLabel = real
    ? edited ? 'Approve with my edits' : 'Approve'
    : edited ? 'Approve with my edits & send' : 'Approve & send'

  async function approve() {
    await api.post(`/api/drafts/${draft.id}/approve`, { final_text: body })
    onDone(real
      ? `Approved “${draft.subject}”. Nothing has been sent: it goes out once sending is switched on.`
      : `Approved “${draft.subject}”.`)
  }

  async function reject() {
    await api.post(`/api/drafts/${draft.id}/reject`, { note })
    onDone(`Rejected “${draft.subject}”.`)
  }

  return (
    <div className="card">
      <p className="muted small">To {draft.to} · {draft.channel === 'email' ? 'Email' : 'Apply link'}</p>
      <h2>{draft.subject}</h2>
      <label htmlFor={`body-${draft.id}`} className="sr-only">Message</label>
      <textarea id={`body-${draft.id}`} rows={10} value={body} onChange={(e) => setBody(e.target.value)} />
      {draft.verifier.checks.length > 0 && (
        <>
          <h3>Checked before you saw it</h3>
          <Checks checks={draft.verifier.checks} />
        </>
      )}

      {rejecting ? (
        <div className="inline-form">
          <label htmlFor={`note-${draft.id}`} className="sr-only">Why not?</label>
          <input id={`note-${draft.id}`} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why not? (helps the next draft)" />
          <button className="btn" onClick={reject}>Reject</button>
          <button className="btn btn-quiet" onClick={() => setRejecting(false)}>Cancel</button>
        </div>
      ) : (
        <div className="actions">
          <button className="btn btn-quiet" onClick={() => setRejecting(true)}>Reject</button>
          <button className="btn btn-primary" onClick={approve}>{approveLabel}</button>
        </div>
      )}
    </div>
  )
}

export function Drafts({ go }: { go: (t: Tab) => void }) {
  const real = useIsReal()
  const { data, error, reload } = useApi<{ drafts: Draft[] }>('/api/drafts?status=pending')
  const [notice, setNotice] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  return (
    <Page title="Drafts" lede="Nothing is sent until you approve it. Your edits teach Chipi your voice.">
      {notice && <p className="job job-done" role="status">{notice}</p>}
      {data.drafts.length === 0 && (
        <Empty title="No drafts waiting"
               action={<button className="btn" onClick={() => go('leads')}>Go to leads</button>}>
          {real ? 'Pick a lead and choose Draft outreach. Chipi writes it and checks it before you see it.' : 'All caught up.'}
        </Empty>
      )}
      {data.drafts.map((d) => (
        <DraftCard key={d.id} draft={d} real={real} onDone={(n) => { setNotice(n); reload() }} />
      ))}
    </Page>
  )
}
