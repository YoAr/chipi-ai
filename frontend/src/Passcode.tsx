import { useEffect, useRef, useState, type FormEvent } from 'react'
import { PASSCODE_NEEDED, api, getPasscode, setPasscode } from './api'

/**
 * Demo passcode (public deploy only). Shows a "view only" note when the server
 * needs a passcode and this browser hasn't entered it, and a dialog whenever a
 * change is refused for lack of it. Renders nothing locally (no passcode set).
 */
export function PasscodeGate() {
  const [required, setRequired] = useState(false)
  const [unlocked, setUnlocked] = useState(() => !!getPasscode())
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.get<{ required: boolean }>('/api/passcode').then((r) => setRequired(r.required)).catch(() => undefined)
    const ask = () => { setUnlocked(false); setDone(false); setOpen(true) }
    window.addEventListener(PASSCODE_NEEDED, ask)
    return () => window.removeEventListener(PASSCODE_NEEDED, ask)
  }, [])

  useEffect(() => { if (open) input.current?.focus() }, [open])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await api.post('/api/passcode/check', { passcode: value })
      setPasscode(value)
      window.dispatchEvent(new Event('chipi:passcode-unlocked'))
      setUnlocked(true)
      setDone(true)
      setValue('')
    } catch (err) {
      setError((err as Error).message === 'This demo needs a passcode to make changes.'
        ? 'That passcode isn’t right.' : (err as Error).message)
    }
  }

  if (!required) return null

  return (
    <>
      {!unlocked && !open && (
        <div className="callout">
          <span>View only. Enter the demo passcode to try things out.</span>
          <button className="btn" onClick={() => setOpen(true)}>Enter passcode</button>
        </div>
      )}
      {open && (
        <div className="dialog-backdrop" onClick={() => setOpen(false)}>
          <div className="card dialog" role="dialog" aria-modal="true" aria-labelledby="pc-title"
               onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}>
            <h2 id="pc-title">{done ? 'Unlocked' : 'Demo passcode'}</h2>
            {done ? (
              <>
                <p className="muted">You can make changes now. Try that again.</p>
                <div className="actions"><button className="btn btn-primary" onClick={() => setOpen(false)}>Continue</button></div>
              </>
            ) : (
              <form onSubmit={submit} className="page">
                <p className="muted">This public demo needs a passcode to change anything.</p>
                <label htmlFor="pc-input" className="sr-only">Passcode</label>
                <input id="pc-input" ref={input} type="password" autoComplete="off" value={value}
                       onChange={(e) => setValue(e.target.value)} placeholder="Passcode" />
                {error && <p className="error small" role="alert">{error}</p>}
                <div className="actions">
                  <button type="button" className="btn btn-quiet" onClick={() => setOpen(false)}>Cancel</button>
                  <button className="btn btn-primary" disabled={!value}>Unlock</button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}
    </>
  )
}
