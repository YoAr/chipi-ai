import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { Markdown } from './Markdown'

type SampleCvDoc = { filename: string; markdown: string }

/**
 * "No CV handy?" row on the profile screen: view Sam's sample CV (fictional project
 * manager in Amsterdam) full screen, download it as a PDF, or load it as if it had been
 * uploaded. Backend: app/routes/sample_cv.py. The parent decides what happens after
 * "Use this CV" (reload the profile, start the build in real mode).
 */
export function SampleCv({ disabled = false, onUsed }: { disabled?: boolean; onUsed: () => void | Promise<void> }) {
  const [doc, setDoc] = useState<SampleCvDoc | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const dialogRef = useRef<HTMLDialogElement>(null)

  // Native modal dialog: traps focus, closes on Esc, and gets a backdrop for free.
  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
    document.body.style.overflow = open ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [open, doc])

  async function view() {
    setError(null)
    if (!doc) {
      try {
        setDoc(await api.get<SampleCvDoc>('/api/sample-cv'))
      } catch (e) {
        setError(`Couldn't open the sample CV: ${(e as Error).message}`)
        return
      }
    }
    setOpen(true)
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

  // Outlined on the profile screen (uploading your own CV is the main action there);
  // the main action inside the pop-up.
  const useButton = (primary: boolean) => (
    <button className={primary ? 'btn btn-primary' : 'btn'} onClick={use} disabled={disabled || busy}>
      {busy ? 'Loading…' : 'Use this CV'}
    </button>
  )

  return (
    <div className="sample-cv">
      <div className="sample-cv-row">
        <span>
          <b>No CV handy?</b> Try Chipi with Sam’s sample CV: a project manager in Amsterdam with 10 years at organisations around the world. Sam isn’t a real person.
        </span>
        <div className="sample-cv-actions">
          <button className="btn btn-quiet" onClick={view} aria-haspopup="dialog">View CV</button>
          <a className="btn btn-quiet" href="/api/sample-cv/pdf" download>PDF</a>
          {useButton(false)}
        </div>
      </div>
      {error && <p className="error small">{error}</p>}

      <dialog
        ref={dialogRef}
        className="cv-modal"
        aria-labelledby="cv-modal-title"
        onClose={() => setOpen(false)}
        onClick={(e) => { if (e.target === e.currentTarget) setOpen(false) }} // click outside the page closes
      >
        <header className="cv-modal-bar">
          <div className="cv-modal-title">
            <b id="cv-modal-title">Sam’s sample CV</b>
            <span className="muted small">Fictional person, for trying Chipi</span>
          </div>
          <div className="sample-cv-actions">
            <a className="btn btn-quiet" href="/api/sample-cv/pdf" download>Download PDF</a>
            {useButton(true)}
            <button className="btn btn-quiet cv-modal-close" onClick={() => setOpen(false)} aria-label="Close">
              <span aria-hidden="true">×</span>
            </button>
          </div>
        </header>
        <div className="cv-modal-body">
          {doc && (
            <article className="cv-doc" aria-label="Sample CV">
              <Markdown text={doc.markdown} rich />
            </article>
          )}
        </div>
      </dialog>
    </div>
  )
}
