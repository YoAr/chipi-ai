import { useState } from 'react'
import { useApi } from '../api'
import type { Metrics } from '../types'
import { Loading, Page } from '../ui'

const pct = (v: number) => `${Math.round(v * 100)}%`
const GUARDRAIL_LABELS: Record<string, string> = {
  unapproved_sends: 'Sent without approval',
  below_floor_quotes: 'Quotes below your floor',
  unsourced_claims: 'Claims without a source',
  duplicate_contacts: 'Same person contacted twice',
}

export function MetricsView() {
  const [simulated, setSimulated] = useState(false)
  const { data, error } = useApi<Metrics>(simulated ? '/api/metrics?simulate_weeks=true' : '/api/metrics')
  if (!data) return <Loading error={error} />
  return (
    <Page title="Metrics" lede={`Week ${data.week}. Ranges show how sure we are; small samples get wide ranges.`}>
      <div className="callout">
        <span>
          {simulated
            ? 'Showing 2 weeks of simulated history (sample data, not saved).'
            : 'See what Chipi learns after two weeks of outreach.'}
        </span>
        <button className="btn btn-primary" onClick={() => setSimulated((s) => !s)}>
          {simulated ? 'Back to today' : 'Simulate 2 weeks'}
        </button>
      </div>
      <div className="grid2">
        {data.headline.map((m) => (
          <div key={m.name} className="card">
            <p className="muted small">{m.name}</p>
            <p className="big">{m.display ?? m.value}</p>
            <p className="small accent">{m.direction === 'up' ? 'Should go up' : 'Should go down'}</p>
          </div>
        ))}
      </div>

      <div className="card">
        <h2>Reply rate by channel</h2>
        {data.channels.map((c) => (
          <div key={c.name} className="interval-row">
            <span className="interval-name">{c.name}</span>
            <div className="interval" role="img" aria-label={`${c.name}: ${pct(c.value)}, likely ${pct(c.low)} to ${pct(c.high)}, ${c.n} sent`}>
              <div className="interval-range" style={{ left: pct(c.low), width: `${(c.high - c.low) * 100}%` }} />
              <div className="interval-point" style={{ left: pct(c.value) }} />
            </div>
            <span className="small">{pct(c.value)} <span className="muted">({c.replies}/{c.sent})</span></span>
          </div>
        ))}
      </div>

      <div className="grid2">
        <div className="card">
          <h2>Guardrails</h2>
          <ul className="plain">
            {Object.entries(data.guardrails).map(([k, v]) => (
              <li key={k} className={v === 0 ? '' : 'error'}><b>{v}</b> · {GUARDRAIL_LABELS[k] ?? k}</li>
            ))}
          </ul>
        </div>
        <div className="card">
          <h2>What Chipi is learning</h2>
          <ul className="plain">
            {data.learning.map((l) => <li key={l.id}>{l.text} <span className="chip">{l.status}</span></li>)}
          </ul>
        </div>
      </div>
    </Page>
  )
}
