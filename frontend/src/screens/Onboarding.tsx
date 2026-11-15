import { useState } from 'react'
import { api, useApi } from '../api'
import type { Tab } from '../App'
import type { Persona } from '../types'
import { Loading, Page } from '../ui'

export function Onboarding({ go }: { go: (t: Tab) => void }) {
  const persona = useApi<Persona>('/api/persona')
  const [status, setStatus] = useState<string | null>(null)

  async function upload(file: File | undefined) {
    if (!file) return
    setStatus(`Reading ${file.name}…`)
    try {
      await api.upload<Persona>('/api/cv', file)
      setStatus(`Added ${file.name}. Your profile is below.`)
      persona.reload()
    } catch (e) {
      setStatus(`Upload failed: ${(e as Error).message}`)
    }
  }

  async function save(field: 'hours_per_week' | 'income_goal_monthly', value: string) {
    const n = value === '' ? null : Number(value)
    if (n !== null && Number.isNaN(n)) return
    await api.put<Persona>('/api/persona', { [field]: n })
    persona.reload()
  }

  return (
    <Page title="Your profile" lede="Upload your CV and answer two questions. Chipi builds the rest.">
      <label className="dropzone" htmlFor="cv">
        <b>Upload your CV</b>
        <span className="muted">PDF or Word. It stays private to you.</span>
        <span className="btn btn-primary">Choose a file</span>
        <input id="cv" className="sr-only" type="file" accept=".pdf,.docx,.txt,.md" onChange={(e) => upload(e.target.files?.[0])} />
      </label>
      {status && <p className="muted">{status}</p>}

      {!persona.data ? (
        <Loading error={persona.error} />
      ) : (
        <div className="card">
          <h2>{persona.data.name}</h2>
          <p>{persona.data.role} · {persona.data.years} years · {persona.data.location}</p>
          <div className="chips">{persona.data.skills.map((s) => <span key={s} className="chip">{s}</span>)}</div>
          <p><b>Wants:</b> {persona.data.wants}</p>
          <p><b>Avoids:</b> {persona.data.avoids}</p>
          <div className="fields">
            <label>
              Hours a week
              <input type="number" min={1} max={80} defaultValue={persona.data.hours_per_week ?? ''} onBlur={(e) => save('hours_per_week', e.target.value)} />
            </label>
            <label>
              Monthly income goal ($)
              <input type="number" min={0} step={500} defaultValue={persona.data.income_goal_monthly ?? ''} onBlur={(e) => save('income_goal_monthly', e.target.value)} />
            </label>
          </div>
          <p className="muted small">Built from: {persona.data.sources.join(', ')}</p>
          <div><button className="btn btn-primary" onClick={() => go('fit')}>Looks right → see my fit and rate</button></div>
        </div>
      )}
    </Page>
  )
}
