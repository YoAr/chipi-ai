import { useState } from 'react'
import { api, useApi } from '../api'
import type { Tab } from '../App'
import { useIsReal } from '../mode'
import type { Draft } from '../types'
import { Checks, Empty, Loading, Page } from '../ui'
import { DraftsV2 } from './DraftsV2'

import './status.css'

const isHttp = (url: string) => /^https?:\/\//i.test(url)

/** Copy text; falls back to a hidden textarea where the Clipboard API is missing (http, old browsers). */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    let ok = false
    try { ok = document.execCommand('copy') } catch { ok = false }
    area.remove()
    return ok
  }
}

const channelLabel = (d: Draft) => (d.channel === 'email' ? 'Email' : 'Apply link')

function DraftCard({ draft, onDone }: { draft: Draft; onDone: (note: string) => void }) {
  const [body, setBody] = useState(draft.body)
  const [rejecting, setRejecting] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const edited = body !== draft.body

  async function approve() {
    setError(null)
    try {
      await api.post(`/api/drafts/${draft.id}/approve`, { final_text: body })
      onDone(`Approved “${draft.subject}”. Nothing has been sent: copy it, send it yourself, then mark it sent.`)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function reject() {
    setError(null)
    try {
      await api.post(`/api/drafts/${draft.id}/reject`, { note })
      onDone(`Rejected “${draft.subject}”.`)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="card">
      <p className="muted small">To {draft.to} · {channelLabel(draft)}</p>
      <h2>{draft.subject}</h2>
      <label htmlFor={`body-${draft.id}`} className="sr-only">Message</label>
      <textarea id={`body-${draft.id}`} rows={10} value={body} onChange={(e) => setBody(e.target.value)} />
      {draft.verifier.checks.length > 0 && (
        <>
          <h3>Checked before you saw it</h3>
          <Checks checks={draft.verifier.checks} />
        </>
      )}
      {error && <p className="error small" role="alert">{error}</p>}

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
          <button className="btn btn-primary" onClick={approve}>{edited ? 'Approve with my edits' : 'Approve'}</button>
        </div>
      )}
    </div>
  )
}

/** An approved draft: the user sends it themselves, then tells Chipi. */
function ReadyCard({ draft, onDone }: { draft: Draft; onDone: (note: string) => void }) {
  const [busy, setBusy] = useState(false)
  const [copyFailed, setCopyFailed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const link = draft.channel === 'apply_link' && isHttp(draft.to) ? draft.to : null

  async function markSent() {
    setBusy(true)
    setError(null)
    try {
      await api.post(`/api/drafts/${draft.id}/sent`, {})
      onDone(`Marked “${draft.subject}” sent. When they answer, record it on the Status page.`)
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  async function copyAndMarkSent() {
    // Start the copy while this tab still has focus, then open the apply link inside the same
    // click (so the browser doesn't block the new tab), then wait for the copy.
    const copying = copyText(draft.body)
    if (link) window.open(link, '_blank', 'noopener,noreferrer')
    if (await copying) {
      await markSent()
    } else {
      setCopyFailed(true) // keep the card (and its text) on screen; never mark sent unseen
    }
  }

  return (
    <div className="card">
      <p className="muted small">
        To {link ? <a href={link} target="_blank" rel="noopener noreferrer">{link}</a> : draft.to} · {channelLabel(draft)}
      </p>
      <h2>{draft.subject}</h2>
      <div className="ready-text" aria-label="Approved message">{draft.body}</div>
      {copyFailed && (
        <p className="job job-error" role="status">
          Couldn’t copy automatically. Select the text above and copy it, send it, then mark it sent.
        </p>
      )}
      {error && <p className="error small" role="alert">{error}</p>}
      <div className="actions">
        {copyFailed && (
          <button className="btn" onClick={markSent} disabled={busy}>I’ve sent it</button>
        )}
        <button className="btn btn-primary" onClick={copyAndMarkSent} disabled={busy}>
          {link ? 'Copy, open link and mark sent' : 'Copy and mark sent'}
        </button>
      </div>
    </div>
  )
}

export function Drafts({ go }: { go: (t: Tab) => void }) {
  const real = useIsReal()
  return real ? <DraftsV2 go={go} /> : <SampleDrafts go={go} />
}

function SampleDrafts({ go }: { go: (t: Tab) => void }) {
  const { data, error, reload } = useApi<{ drafts: Draft[] }>('/api/drafts')
  const [notice, setNotice] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  const pending = data.drafts.filter((d) => d.status === 'pending')
  const ready = data.drafts.filter((d) => d.status === 'approved')
  const done = (n: string) => { setNotice(n); reload() }
  return (
    <Page title="Drafts" lede="Nothing is sent until you approve it. Your edits teach Chipi your voice.">
      {notice && <p className="job job-done" role="status">{notice}</p>}
      {pending.length === 0 && ready.length === 0 && (
        <Empty title="No drafts waiting"
               action={<button className="btn" onClick={() => go('leads')}>Go to leads</button>}>
          All caught up.
        </Empty>
      )}
      {ready.length > 0 && (
        <section className="drafts-group" aria-labelledby="ready-head">
          <h2 id="ready-head" className="status-label">Approved: send these yourself</h2>
          <p className="muted small">Chipi doesn’t send email. Copy the message, send it, and Chipi starts counting replies.</p>
          {ready.map((d) => <ReadyCard key={d.id} draft={d} onDone={done} />)}
        </section>
      )}
      {pending.length > 0 && ready.length > 0 && <h2 className="status-label">Waiting for your approval</h2>}
      {pending.map((d) => <DraftCard key={d.id} draft={d} onDone={done} />)}
    </Page>
  )
}
