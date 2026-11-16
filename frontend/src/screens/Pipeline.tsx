import type { Tab } from '../App'
import { useApi } from '../api'
import type { Pipeline } from '../types'
import { Loading, Page } from '../ui'

/**
 * Status board. Same data, two arrangements (CSS picks one at 840px):
 * - phone: stage counts, then one list split into "Needs you" and "In progress";
 * - desktop: one lane per stage, lanes separated by thin lines, items as cards.
 * Deadlines are shown on the item they belong to, as a date chip.
 */
type Item = {
  key: string
  title: string
  note: string
  stage: number // index into data.columns
  stageName: string
  due?: { what: string; date: string } // earliest open deadline
  action?: { label: string; to: Tab }
}

// "2026-11-18" -> "Tue 18 Nov" (read as a local date, so it never shifts a day).
function shortDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  if (!y || !m || !d) return iso
  return new Date(y, m - 1, d).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
}

// What, if anything, the user should do next. Green link on the card; "Needs you" on phones.
function nextAction(stageName: string, note: string): Item['action'] {
  if (stageName === 'Drafted') return { label: 'Review', to: 'drafts' }
  if (stageName === 'Sent' && /no reply/i.test(note)) return { label: 'Follow up', to: 'drafts' }
  return undefined
}

function Card({ item, stages, go, showStage }: { item: Item; stages: number; go: (t: Tab) => void; showStage: boolean }) {
  return (
    <div className="bcard">
      <b className="bcard-title">{item.title}</b>
      {item.note && <span className="muted small">{item.note}</span>}
      {(showStage || item.due || item.action) && (
        <span className="bcard-foot">
          {showStage && (
            <span className="bcard-stage" aria-label={`Stage ${item.stage + 1} of ${stages}: ${item.stageName}`}>
              <span className="dashes" aria-hidden="true">
                {Array.from({ length: stages }, (_, i) => <i key={i} className={i <= item.stage ? 'on' : undefined} />)}
              </span>
              {item.stageName}
            </span>
          )}
          {item.due && <span className="due" title={item.due.what}>{shortDate(item.due.date)}</span>}
          {item.action && <button className="link-act" onClick={() => go(item.action!.to)}>{item.action.label} →</button>}
        </span>
      )}
    </div>
  )
}

export function PipelineView({ go }: { go: (t: Tab) => void }) {
  const { data, error } = useApi<Pipeline>('/api/pipeline')
  if (!data) return <Loading error={error} />

  const firstDue = new Map<string, { what: string; date: string }>()
  for (const d of [...data.deadlines].sort((a, b) => a.due.localeCompare(b.due))) {
    if (!firstDue.has(d.nickname)) firstDue.set(d.nickname, { what: d.what, date: d.due })
  }
  const lanes = data.columns.map((c, stage) => ({
    name: c.name,
    items: c.items.map((i): Item => ({
      key: i.nickname,
      title: i.title || i.nickname,
      note: i.note,
      stage,
      stageName: c.name,
      due: firstDue.get(i.nickname),
      action: nextAction(c.name, i.note),
    })),
  }))
  const all = lanes.flatMap((l) => l.items)
  const byDue = (a: Item, b: Item) => (a.due?.date ?? '9999').localeCompare(b.due?.date ?? '9999') || a.stage - b.stage
  const isDone = (i: Item) => i.stage === lanes.length - 1
  const needsYou = all.filter((i) => i.action).sort(byDue)
  const inProgress = all.filter((i) => !i.action && !isDone(i)).sort(byDue)
  const done = all.filter((i) => !i.action && isDone(i))
  const onBoard = new Set(all.map((i) => i.key))
  const otherDeadlines = data.deadlines.filter((d) => !onBoard.has(d.nickname))
  const stages = lanes.length

  return (
    <Page title="Status" lede="Every lead from draft to done.">
      {/* Phone: counts, then what needs you first */}
      <div className="status-phone">
        <div className="stage-strip">
          {lanes.map((l) => (
            <span key={l.name} className={l.items.length ? undefined : 'zero'}><b>{l.items.length}</b>{l.name}</span>
          ))}
        </div>
        {needsYou.length > 0 && (
          <section className="status-group">
            <h2 className="status-label">Needs you</h2>
            {needsYou.map((i) => <Card key={i.key} item={i} stages={stages} go={go} showStage />)}
          </section>
        )}
        {inProgress.length > 0 && (
          <section className="status-group">
            <h2 className="status-label">In progress</h2>
            {inProgress.map((i) => <Card key={i.key} item={i} stages={stages} go={go} showStage />)}
          </section>
        )}
        {done.length > 0 && (
          <details className="status-group">
            <summary className="status-label">Done ({done.length})</summary>
            {done.map((i) => <Card key={i.key} item={i} stages={stages} go={go} showStage={false} />)}
          </details>
        )}
        {all.length === 0 && <p className="muted">Nothing here yet. Approve a draft and it shows up here.</p>}
      </div>

      {/* Desktop: one lane per stage */}
      <div className="lanes" style={{ gridTemplateColumns: `repeat(${stages}, minmax(0, 1fr))` }}>
        {lanes.map((l) => (
          <section key={l.name} className="lane" aria-label={l.name}>
            <h2 className="lane-head">{l.name} <span>{l.items.length}</span></h2>
            {l.items.map((i) => <Card key={i.key} item={i} stages={stages} go={go} showStage={false} />)}
            {l.items.length === 0 && <p className="lane-empty">{l.name === 'Done' ? 'Nothing finished yet' : 'Nothing here'}</p>}
          </section>
        ))}
      </div>

      {otherDeadlines.length > 0 && (
        <p className="muted small">
          Also due: {otherDeadlines.map((d) => `${d.what} (${shortDate(d.due)})`).join(' · ')}
        </p>
      )}
    </Page>
  )
}
