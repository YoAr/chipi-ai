import type { ReactNode } from 'react'
import type { Check, Job } from './types'

/** One line of progress for a background action; quiet when idle. */
export function JobStatus({ job, startError }: { job: Job | null; startError?: string | null }) {
  if (startError) return <p className="job job-error" role="status">{startError}</p>
  if (!job || job.status === 'idle') return null
  if (job.status === 'running') {
    return <p className="job job-running" role="status"><span className="spinner" aria-hidden="true" />{job.message ?? 'Working…'}</p>
  }
  if (job.status === 'error') {
    const notBuilt = job.error?.startsWith('Not built yet')
    return (
      <p className="job job-error" role="status">
        {notBuilt ? 'This part of Chipi isn’t switched on yet. It’s being built right now.' : `That didn’t work: ${job.error}`}
      </p>
    )
  }
  return job.message ? <p className="job job-done" role="status">{job.message}</p> : null
}

/** A screen with nothing to show yet: say why and offer the next step. */
export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="card empty">
      <h2>{title}</h2>
      {children && <p className="muted">{children}</p>}
      {action && <div>{action}</div>}
    </div>
  )
}

export function Page({ title, lede, children }: { title: string; lede?: string; children: ReactNode }) {
  return (
    <section className="page">
      <div className="page-head">
        <h1>{title}</h1>
        {lede && <p className="lede">{lede}</p>}
      </div>
      {children}
    </section>
  )
}

export function Loading({ error }: { error: string | null }) {
  return error ? <p className="error">Couldn’t load this: {error}</p> : <p className="muted">Loading…</p>
}

export function Checks({ checks }: { checks: Check[] }) {
  return (
    <ul className="checks">
      {checks.map((c) => (
        <li key={c.name} className={c.passed ? 'check-pass' : 'check-fail'}>
          <span className="check-mark">{c.passed ? 'Pass' : 'Fail'}</span>
          {c.name}
          {c.note ? <span className="muted"> · {c.note}</span> : null}
        </li>
      ))}
    </ul>
  )
}

const SOURCE_LABELS: Record<string, string> = {
  hn_whos_hiring: "HN Who's Hiring",
  web_search: 'Web search',
  remotive: 'Remotive',
  remoteok: 'RemoteOK',
  seed: 'Pasted link',
}
export const sourceLabel = (s: string) => SOURCE_LABELS[s] ?? s
