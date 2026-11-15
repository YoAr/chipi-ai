import { useApi } from '../api'
import type { Tab } from '../App'
import type { Fit } from '../types'
import { Checks, Empty, Loading, Page } from '../ui'

const SCALE_MAX = 200 // $/hour shown on the rate bar

export function FitRate({ go }: { go: (t: Tab) => void }) {
  const { data, error } = useApi<Fit>('/api/fit')
  if (!data) return <Loading error={error} />
  const { rate } = data
  const pct = (v: number) => `${Math.min(100, (v / SCALE_MAX) * 100)}%`

  if (rate.low == null || rate.high == null || rate.ask == null || rate.floor == null) {
    return (
      <Page title="Fit & rate" lede="What to sell, what to charge, and why.">
        <Empty title="No rate yet"
               action={<button className="btn btn-primary" onClick={() => go('onboarding')}>Go to your profile</button>}>
          Chipi works out where you fit and what to charge once it has read your CV.
          {rate.floor != null && ` It will never suggest less than your floor of $${rate.floor}/hour.`}
        </Empty>
      </Page>
    )
  }

  return (
    <Page title="Fit & rate" lede="What to sell, what to charge, and why.">
      <div className="grid2">
        <div className="card">
          <h2>Where you fit</h2>
          {data.fits.map((f) => (
            <div key={f.name} className="fit">
              <span className={`level level-${f.level}`}>{f.level}</span>
              <div>
                <b>{f.name}</b>
                <p className="muted small">{f.reason}</p>
              </div>
            </div>
          ))}
        </div>

        <div className="card">
          <h2>Suggested rate</h2>
          <p className="big">${rate.low}–{rate.high} <span className="muted">/ {rate.unit}</span></p>
          <div className="ratebar" role="img" aria-label={`Suggested $${rate.low} to $${rate.high}, floor $${rate.floor}, ask $${rate.ask}`}>
            <div className="ratebar-band" style={{ left: pct(rate.low), width: `calc(${pct(rate.high)} - ${pct(rate.low)})` }} />
            <div className="ratebar-floor" style={{ left: pct(rate.floor) }} title={`Floor $${rate.floor}`} />
          </div>
          <div className="ratebar-scale muted small"><span>$0</span><span>${SCALE_MAX / 2}</span><span>${SCALE_MAX}/hr</span></div>
          <p className="small">Ask <b>${rate.ask}</b> · never below your floor of <b>${rate.floor}</b></p>
          <h3>Why this number</h3>
          <ul className="reasons">
            {rate.reasons.map((r) => (
              <li key={r.text}>{r.text} <span className="muted small">({r.source})</span></li>
            ))}
          </ul>
        </div>
      </div>

      {data.goal_checks.length > 0 && (
        <div className="card">
          <h3>Checks before showing you this</h3>
          <Checks checks={data.goal_checks} />
        </div>
      )}
    </Page>
  )
}
