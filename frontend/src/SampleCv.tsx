import { useState } from 'react'
import { api } from './api'
import { Markdown } from './Markdown'

type SampleCvDoc = { filename: string; markdown: string }

/**
 * "No CV handy?" row on the profile screen: preview Sam's sample CV (fictional project
 * manager in Amsterdam) or load it as if it had been uploaded. Backend: app/routes/sample_cv.py.
 * The parent decides what happens next (reload the profile, start the build in real mode).
 */
export function SampleCv({ disabled = false, onUsed }: { disabled?: boolean; onUsed: () => void | Promise<void> }) {
  const [doc, setDoc] = useState<SampleCvDoc | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function toggle() {
    if (!open && !doc) {
      try {
        setDoc(await api.get<SampleCvDoc>('/api/sample-cv'))
      } catch (e) {
        setError((e as Error).message)
        return
      }
    }
    setOpen((o) => !o)
  }

  async function use() {
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/sample-cv/use', {})
      setOpen(false)
      await onUsed()
    } catch (e) {
      setError(`Couldn't load the sample CV: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sample-cv">
      <div className="sample-cv-row">
        <span>
          <b>No CV handy?</b> Try Chipi with Sam’s sample CV: a project manager in Amsterdam with 10 years at organisations around the world. Sam isn’t a real person.
        </span>
        <div className="sample-cv-actions">
          <button className="btn btn-quiet" onClick={toggle} aria-expanded={open} aria-controls="sample-cv-doc">
            {open ? 'Hide CV' : 'View CV'}
          </button>
          <a className="btn btn-quiet" href="/api/sample-cv/pdf" download>PDF</a>
          <button className="btn" onClick={use} disabled={disabled || busy}>
            {busy ? 'Loading…' : 'Use this CV'}
          </button>
        </div>
      </div>
      {error && <p className="error small">{error}</p>}
      {open && doc && (
        <article id="sample-cv-doc" className="cv-doc" aria-label="Sample CV">
          <Markdown text={doc.markdown} rich />
        </article>
      )}
    </div>
  )
}
