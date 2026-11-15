import { useState, type FormEvent } from 'react'
import { api, useApi } from '../api'
import type { Lead } from '../types'
import { Loading, Page, sourceLabel } from '../ui'

const VERDICTS = [
  ['useful', 'Useful'],
  ['maybe', 'Maybe'],
  ['skip', 'Skip'],
] as const

export function Leads() {
  const { data, error, reload } = useApi<{ leads: Lead[] }>('/api/leads')
  const [url, setUrl] = useState('')
  const [msg, setMsg] = useState<string | null>(null)

  async function verdict(id: string, v: string) {
    await api.post(`/api/leads/${id}/verdict`, { verdict: v })
    reload()
  }

  async function addSeed(e: FormEvent) {
    e.preventDefault()
    try {
      await api.post('/api/seeds', { url })
      setUrl('')
      setMsg('Added. Chipi will fetch and score it.')
      reload()
    } catch (err) {
      setMsg((err as Error).message)
    }
  }

  return (
    <Page title="Leads" lede="Ranked by how well they fit you. Your verdicts teach the next search.">
      <form className="inline-form" onSubmit={addSeed}>
        <label htmlFor="seed" className="sr-only">Job post link</label>
        <input id="seed" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Found a job post? Paste the link" />
        <button className="btn">Add</button>
      </form>
      {msg && <p className="muted small">{msg}</p>}

      {!data ? (
        <Loading error={error} />
      ) : (
        <div className="list">
          {data.leads.map((l) => (
            <div key={l.id} className="row row-lead">
              <span className="score" title="Match score">{l.score ?? '—'}</span>
              <div className="grow">
                {/^https?:\/\//i.test(l.url) ? (
                  <a href={l.url} target="_blank" rel="noreferrer"><b>{l.title}</b></a>
                ) : (
                  <b>{l.title}</b>
                )}
                <p className="muted small">
                  {sourceLabel(l.source)} · {l.rate_text ?? 'rate unknown'}
                  {!l.fetched && ' · not fetched yet'}
                </p>
                <p className="small">Why: {l.why}</p>
              </div>
              <div className="seg" role="group" aria-label={`Verdict for ${l.title}`}>
                {VERDICTS.map(([v, label]) => (
                  <button key={v} className={l.status === v ? 'seg-btn seg-on' : 'seg-btn'} aria-pressed={l.status === v} onClick={() => verdict(l.id, v)}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Page>
  )
}
