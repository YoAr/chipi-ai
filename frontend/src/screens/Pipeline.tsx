import { useApi } from '../api'
import type { Pipeline } from '../types'
import { Loading, Page } from '../ui'

export function PipelineView() {
  const { data, error } = useApi<Pipeline>('/api/pipeline')
  if (!data) return <Loading error={error} />
  return (
    <Page title="Pipeline" lede="Every lead from draft to done, and what’s due.">
      <div className="board">
        {data.columns.map((c) => (
          <div key={c.name} className="col">
            <h3>{c.name} <span className="muted">{c.items.length}</span></h3>
            {c.items.map((i) => (
              <div key={i.nickname} className="tile">
                <b>{i.nickname}</b>
                <p className="muted small">{i.note}</p>
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="card">
        <h2>Deadlines</h2>
        <ul className="plain">
          {data.deadlines.map((d) => (
            <li key={d.nickname + d.what}><b>{d.due}</b> · {d.what} · {d.nickname}</li>
          ))}
        </ul>
      </div>
    </Page>
  )
}
