import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { InterviewState } from './types'
import { JobStatus } from './ui'

const POLL_MS = 1500

/** Spec v2 Interviewer: one question at a time, tied to a line of the CV, with progress. */
export function Interview({ hasCv, onProfileReady }: { hasCv: boolean; onProfileReady: () => void }) {
  const [state, setState] = useState<InterviewState | null>(null)
  const [answer, setAnswer] = useState('')
  const [language, setLanguage] = useState<'en' | 'nl'>('en')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | null>(null)
  const logEnd = useRef<HTMLDivElement | null>(null)

  async function load() {
    try {
      const s = await api.get<InterviewState>('/api/interview')
      setState(s)
      if (s.job.status === 'running') timer.current = window.setTimeout(load, POLL_MS)
      else if (s.status === 'extracted' && s.job.status === 'done') onProfileReady()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  useEffect(() => {
    load()
    return () => { if (timer.current) window.clearTimeout(timer.current) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { logEnd.current?.scrollIntoView({ block: 'nearest' }) }, [state?.turns.length])

  async function act(path: string, body: unknown) {
    setBusy(true)
    setError(null)
    try {
      const s = await api.post<InterviewState>(path, body)
      setState(s)
      setAnswer('')
      if (s.status === 'complete' || s.job.status === 'running') timer.current = window.setTimeout(load, POLL_MS)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!state) return error ? <p className="error">Couldn’t load the interview: {error}</p> : null

  if (state.status === 'not_started') {
    return (
      <div className="card">
        <h2>Short interview</h2>
        <p className="muted">
          About 10 questions about your CV: results you can prove, clients you may name, the work you want and your
          minimum rate. Chipi builds your profile from your answers only.
        </p>
        <div className="fields">
          <label>
            Language
            <select value={language} onChange={(e) => setLanguage(e.target.value as 'en' | 'nl')}>
              <option value="en">English</option>
              <option value="nl">Nederlands</option>
            </select>
          </label>
        </div>
        <div>
          <button className="btn btn-primary" disabled={!hasCv || busy} onClick={() => act('/api/interview/start', { language })}>
            {busy ? 'Reading your CV…' : 'Start the interview'}
          </button>
        </div>
        {!hasCv && <p className="muted small">Upload your CV first.</p>}
        {error && <p className="error">{error}</p>}
      </div>
    )
  }

  const current = state.turns[state.turns.length - 1]
  const active = state.status === 'active' && current && current.answer === null
  const p = state.progress

  return (
    <div className="card interview">
      <div className="interview-head">
        <h2>Interview</h2>
        {p && state.status === 'active' && (
          <span className="muted small" role="status">
            Question {p.asked} · about {Math.max(p.left, 1)} left
          </span>
        )}
      </div>
      <div className="chat-log interview-log" aria-live="polite">
        {state.turns.map((t, i) => (
          <div key={i} className="interview-turn">
            {t.reflection && <div className="msg">{t.reflection}</div>}
            <div className="msg">
              {t.resume_line && <span className="quote small">“{t.resume_line}”</span>}
              {t.question}
            </div>
            {t.answer !== null && <div className="msg msg-user">{t.answer}</div>}
          </div>
        ))}
        <div ref={logEnd} />
      </div>
      {active ? (
        <form className="interview-form" onSubmit={(e) => { e.preventDefault(); if (answer.trim()) act('/api/interview/answer', { answer }) }}>
          <label htmlFor="interview-answer" className="sr-only">Your answer</label>
          <textarea id="interview-answer" rows={3} value={answer} maxLength={4000} disabled={busy}
                    onChange={(e) => setAnswer(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && answer.trim()) act('/api/interview/answer', { answer }) }} />
          <div className="row-actions">
            <button className="btn btn-primary" disabled={busy || !answer.trim()}>{busy ? 'Thinking…' : 'Answer'}</button>
            <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => act('/api/interview/finish', {})}>
              I’ve said enough, build my profile
            </button>
          </div>
        </form>
      ) : (
        <JobStatus job={state.job} />
      )}
      {state.status === 'extracted' && state.job.status !== 'running' && (
        <div>
          <button className="btn" disabled={busy} onClick={() => act('/api/interview/finish', {})}>Rebuild my profile from this interview</button>
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  )
}
