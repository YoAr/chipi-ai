import type { ReactNode } from 'react'
import type { Check } from './types'

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
