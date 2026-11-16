import { useState } from 'react'
import { api, useApi, useJob } from '../api'
import type { Tab } from '../App'
import { useIsReal } from '../mode'
import { SampleCv } from '../SampleCv'
import type { Persona } from '../types'
import { Empty, JobStatus, Loading, Page } from '../ui'

export function Onboarding({ go }: { go: (t: Tab) => void }) {
  const real = useIsReal()
  const persona = useApi<Persona>('/api/persona')
  const [status, setStatus] = useState<string | null>(null)
  const build = useJob('/api/profile/build', '/api/profile/status', () => persona.reload(), real)

  async function upload(file: File | undefined) {
    if (!file) return
    setStatus(`Uploading ${file.name}…`)
    try {
      await api.upload<Persona>('/api/cv', file)
      persona.reload()
      if (real) {
        setStatus(null)
        await build.start() // CV → persona → fit & rate → offers
      } else {
        setStatus(`Added ${file.name}. Your profile is below.`)
      }
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

  const p = persona.data
  const hasProfile = !!p && (p.name || p.role || p.skills.length > 0)
  const hasCv = !!p && p.sources.length > 0
  const facts = p ? [p.role, p.years != null ? `${p.years} years` : '', p.location].filter(Boolean).join(' · ') : ''

  return (
    <Page title="Your profile" lede="Upload your CV and answer two questions. Chipi builds the rest.">
      <label className="dropzone" htmlFor="cv">
        <b>{hasCv ? 'Upload a newer CV' : 'Upload your CV'}</b>
        <span className="muted">PDF or Word. It stays private to you.</span>
        <span className={hasCv ? 'btn' : 'btn btn-primary'}>Choose a file</span>
        <input id="cv" className="sr-only" type="file" accept=".pdf,.docx,.txt,.md" disabled={build.running}
               onChange={(e) => upload(e.target.files?.[0])} />
      </label>
      <SampleCv disabled={build.running} onUsed={async () => { persona.reload(); setStatus(null); if (real) await build.start() }} />
      {status && <p className="muted">{status}</p>}
      <JobStatus job={build.job} startError={build.startError} />
      {real && hasCv && !build.running && build.job?.status !== 'done' && (
        <div><button className="btn" onClick={() => build.start()}>Rebuild my profile from this CV</button></div>
      )}

      {!p ? (
        <Loading error={persona.error} />
      ) : !hasProfile ? (
        <Empty title="No profile yet">
          {hasCv ? 'Your CV is uploaded. Your profile appears here once Chipi has read it.' : 'Upload your CV above and Chipi reads it for you.'}
        </Empty>
      ) : (
        <div className="card">
          <h2>{p.name || 'Your profile'}</h2>
          {facts && <p>{facts}</p>}
          {p.skills.length > 0 && <div className="chips">{p.skills.map((s) => <span key={s} className="chip">{s}</span>)}</div>}
          {p.wants && <p><b>Wants:</b> {p.wants}</p>}
          {p.avoids && <p><b>Avoids:</b> {p.avoids}</p>}
          <div className="fields">
            <label>
              Hours a week
              <input type="number" min={1} max={80} defaultValue={p.hours_per_week ?? ''} onBlur={(e) => save('hours_per_week', e.target.value)} />
            </label>
            <label>
              Monthly income goal ($)
              <input type="number" min={0} step={500} defaultValue={p.income_goal_monthly ?? ''} onBlur={(e) => save('income_goal_monthly', e.target.value)} />
            </label>
          </div>
          {p.sources.length > 0 && <p className="muted small">Built from: {p.sources.join(', ')}</p>}
          <div><button className="btn btn-primary" onClick={() => go('fit')}>Looks right → see my fit and rate</button></div>
        </div>
      )}
    </Page>
  )
}
