import { useEffect, useMemo, useRef, useState } from 'react'
import { useApi } from '../api'
import type { Metric, Metrics } from '../types'
import { Loading, Page } from '../ui'
import './metrics.css'

type Channel = Metrics['channels'][number]
type Lesson = Metrics['learning'][number]
type Snapshot = { day: number; headline: Metric[]; channels: Channel[]; learning: Lesson[]; guardrails: Record<string, number> }
type Simulated = Metrics & { simulated: true; timeline: Snapshot[] }

const STEP_MS = 230 // one simulated day per step: 14 days ≈ 3 seconds
const pct = (v: number) => `${Math.round(v * 100)}%`
const GUARDRAIL_LABELS: Record<string, string> = {
  unapproved_sends: 'Sent without approval',
  below_floor_quotes: 'Quotes below your floor',
  unsourced_claims: 'Claims without a source',
  duplicate_contacts: 'Same person contacted twice',
}
const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

/** A number that glides to its new value (instant when the viewer prefers less motion). */
function useTween(target: number | null, ms = 400): number | null {
  const [shown, setShown] = useState(target)
  const from = useRef(target)
  useEffect(() => {
    if (target == null || from.current == null || reducedMotion()) {
      from.current = target
      setShown(target)
      return
    }
    const start = performance.now()
    const a = from.current
    let raf = 0
    const tick = (now: number) => {
      const k = Math.min(1, (now - start) / ms)
      const v = a + (target - a) * (1 - (1 - k) ** 3)
      setShown(v)
      if (k < 1) raf = requestAnimationFrame(tick)
      else from.current = target
    }
    raf = requestAnimationFrame(tick)
    return () => { cancelAnimationFrame(raf); from.current = target }
  }, [target, ms])
  return shown
}

function Headline({ m }: { m: Metric }) {
  const isIncome = m.name.toLowerCase().includes('income')
  const v = useTween(m.value ?? null)
  const text = v == null ? (m.display ?? '—') : isIncome ? `${Math.round(v * 100)}% of goal` : `${Math.round(v)} min`
  return (
    <div className="card">
      <p className="muted small">{m.name}</p>
      <p className="big">{text}</p>
      <p className="small accent">{m.direction === 'up' ? 'Should go up' : 'Should go down'}</p>
    </div>
  )
}

/** "A replies more than B" → what the next search does differently. */
function nextStep(lesson: string): string | null {
  const m = lesson.match(/^(.+) replies more than (.+)$/)
  return m ? `Next search leans on ${m[1]} and spends less effort on ${m[2]}.` : null
}

export function MetricsView() {
  const today = useApi<Metrics>('/api/metrics')
  const sim = useApi<Simulated>('/api/metrics?simulate_weeks=true') // cached on the server
  const [view, setView] = useState<'today' | 'sim'>('today')
  const [day, setDay] = useState(14)
  const [playing, setPlaying] = useState(false)

  const timeline = sim.data?.timeline ?? []
  const lastDay = timeline.length ? timeline[timeline.length - 1].day : 14

  useEffect(() => {
    if (!playing) return
    if (day >= lastDay) { setPlaying(false); return }
    const t = window.setTimeout(() => setDay((d) => d + 1), STEP_MS)
    return () => window.clearTimeout(t)
  }, [playing, day, lastDay])

  // When each lesson first appeared, and whether it still holds at the end
  const lessonDays = useMemo(() => {
    const first = new Map<string, number>()
    for (const p of timeline) for (const l of p.learning) if (!first.has(l.text)) first.set(l.text, p.day)
    return first
  }, [timeline])

  if (!today.data) return <Loading error={today.error} />
  const snapshot: Snapshot | null = view === 'sim' ? timeline.find((p) => p.day === day) ?? null : null
  const data = snapshot ?? { ...today.data, day: 0 }

  // Stable rows across the whole animation, so bars move instead of jumping around
  const names = Array.from(new Set([...(sim.data?.channels ?? []), ...today.data.channels].map((c) => c.name)))
  const byName = new Map(data.channels.map((c) => [c.name, c]))
  const sent = data.channels.reduce((s, c) => s + c.sent, 0)
  const replies = data.channels.reduce((s, c) => s + c.replies, 0)
  const newToday = snapshot ? snapshot.learning.filter((l) => lessonDays.get(l.text) === snapshot.day) : []
  const finished = view === 'sim' && day === lastDay && !playing
  const best = [...data.channels].filter((c) => c.sent > 0).sort((a, b) => b.value - a.value)

  const headlineCards = (
    <div className="grid2">
      {data.headline.map((m) => <Headline key={m.name.startsWith('User minutes') ? 'minutes' : m.name} m={m} />)}
    </div>
  )

  function play() {
    setView('sim')
    setDay(0)
    setPlaying(true)
  }

  return (
    <Page title="Metrics" lede={view === 'sim'
      ? 'A simulated two weeks of outreach, day by day. Sample history, not your data, and nothing is saved.'
      : 'Ranges show how sure we are; small samples get wide ranges.'}>
      <div className={view === 'sim' ? 'sim-bar sim-bar-on' : 'sim-bar'}>
        <div className="seg" role="group" aria-label="Which data to show">
          <button className={view === 'today' ? 'seg-btn seg-on' : 'seg-btn'} aria-pressed={view === 'today'}
                  onClick={() => { setPlaying(false); setView('today') }}>Today</button>
          <button className={view === 'sim' ? 'seg-btn seg-on' : 'seg-btn'} aria-pressed={view === 'sim'} disabled={!sim.data}
                  onClick={() => { setPlaying(false); setView('sim'); setDay(lastDay) }}>2 weeks later</button>
        </div>
        <button className="btn" onClick={play} disabled={!sim.data || playing}>
          {playing ? 'Playing…' : view === 'sim' ? 'Replay the 2 weeks' : 'Simulate 2 weeks'}
        </button>
        {view === 'sim' && (
          <label className="scrubber">
            <span className="scrubber-day">Day {day} of {lastDay}</span>
            <input type="range" min={0} max={lastDay} value={day} aria-label="Simulated day"
                   onChange={(e) => { setPlaying(false); setDay(Number(e.target.value)) }} />
          </label>
        )}
      </div>

      {newToday.map((l) => (
        <div key={`${l.text}-${day}`} className="learned-flash" role="status">
          <b>Day {day}: Chipi learned something.</b> {l.text}.
        </div>
      ))}

      {view === 'today' && headlineCards}

      <div className="card">
        <h2>Reply rate by channel</h2>
        {view === 'sim' && <p className="muted small">{sent} sent · {replies} replies so far</p>}
        {names.length === 0 && <p className="muted">No outreach yet. Rates appear once messages go out.</p>}
        {names.map((name) => {
          const c = byName.get(name)
          const has = !!c && c.sent > 0
          return (
            <div key={name} className="interval-row">
              <span className="interval-name">{name}</span>
              <div className="interval" role="img" aria-label={has ? `${name}: ${pct(c!.value)}, likely ${pct(c!.low)} to ${pct(c!.high)}, ${c!.sent} sent` : `${name}: nothing sent yet`}>
                <div className="interval-range anim" style={{ left: has ? pct(c!.low) : '0%', width: has ? `${(c!.high - c!.low) * 100}%` : '0%' }} />
                <div className={has ? 'interval-point anim' : 'interval-point anim interval-point-off'} style={{ left: has ? pct(c!.value) : '0%' }} />
              </div>
              <span className="small">{has ? <>{pct(c!.value)} <span className="muted">({c!.replies}/{c!.sent})</span></> : <span className="muted">not yet</span>}</span>
            </div>
          )
        })}
      </div>

      {view === 'sim' && headlineCards}

      {finished && sim.data && (
        <div className="card summary">
          <h2>What changed in two weeks</h2>
          <ul className="plain">
            <li><b>{sent}</b> messages sent, <b>{replies}</b> replies.</li>
            {best.length > 1 && (
              <li>Best channel: <b>{best[0].name}</b> ({pct(best[0].value)}). Weakest: <b>{best[best.length - 1].name}</b> ({pct(best[best.length - 1].value)}).</li>
            )}
            {sim.data.learning.map((l) => (
              <li key={l.text}>Learned on day {lessonDays.get(l.text)}, still holding: <b>{l.text}</b>. {nextStep(l.text)}</li>
            ))}
            {sim.data.learning.length === 0 && <li>Not enough evidence yet to change anything. Chipi waits rather than guess.</li>}
          </ul>
        </div>
      )}

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
          {data.learning.length === 0 && (
            <p className="muted small">{view === 'sim' ? 'Watching. A lesson needs at least 5 sends per channel and 80% confidence.' : 'Nothing yet.'}</p>
          )}
          <ul className="plain">
            {data.learning.map((l) => (
              <li key={l.text} className="lesson">{l.text} <span className="chip">{l.status}</span></li>
            ))}
          </ul>
        </div>
      </div>
    </Page>
  )
}
