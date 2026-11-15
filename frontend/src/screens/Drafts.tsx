import { useState } from 'react'
import { api, useApi } from '../api'
import type { Draft } from '../types'
import { Checks, Loading, Page } from '../ui'

function DraftCard({ draft, onDone }: { draft: Draft; onDone: () => void }) {
  const [body, setBody] = useState(draft.body)
  const [rejecting, setRejecting] = useState(false)
  const [note, setNote] = useState('')
  const edited = body !== draft.body

  return (
    <div className="card">
      <p className="muted small">To {draft.to} · {draft.channel === 'email' ? 'Email' : 'Apply link'}</p>
      <h2>{draft.subject}</h2>
      <label htmlFor={`body-${draft.id}`} className="sr-only">Message</label>
      <textarea id={`body-${draft.id}`} rows={10} value={body} onChange={(e) => setBody(e.target.value)} />
      <h3>Checked before you saw it</h3>
      <Checks checks={draft.verifier.checks} />

      {rejecting ? (
        <div className="inline-form">
          <label htmlFor={`note-${draft.id}`} className="sr-only">Why not?</label>
          <input id={`note-${draft.id}`} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why not? (helps the next draft)" />
          <button className="btn" onClick={async () => { await api.post(`/api/drafts/${draft.id}/reject`, { note }); onDone() }}>Reject</button>
          <button className="btn btn-quiet" onClick={() => setRejecting(false)}>Cancel</button>
        </div>
      ) : (
        <div className="actions">
          <button className="btn btn-quiet" onClick={() => setRejecting(true)}>Reject</button>
          <button className="btn btn-primary" onClick={async () => { await api.post(`/api/drafts/${draft.id}/approve`, { final_text: body }); onDone() }}>
            {edited ? 'Approve with my edits & send' : 'Approve & send'}
          </button>
        </div>
      )}
    </div>
  )
}

export function Drafts() {
  const { data, error, reload } = useApi<{ drafts: Draft[] }>('/api/drafts?status=pending')
  if (!data) return <Loading error={error} />
  return (
    <Page title="Drafts" lede="Nothing is sent until you approve it. Your edits teach Chipi your voice.">
      {data.drafts.length === 0 && <p className="muted">All caught up. New drafts appear after the morning run.</p>}
      {data.drafts.map((d) => <DraftCard key={d.id} draft={d} onDone={reload} />)}
    </Page>
  )
}
