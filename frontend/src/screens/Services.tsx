import { useState } from 'react'
import { api, useApi, useJob } from '../api'
import type { Tab } from '../App'
import type { ProfileDoc, ServiceItem, ServicesDoc } from '../types'
import { Checks, Empty, JobStatus, Loading, Page } from '../ui'

const STRENGTH: Record<string, string> = { claimed: 'Claimed', demonstrated: 'Demonstrated', referenceable: 'Referenceable' }

/** Spec v2 Offer designer: services ranked by hiring demand, reviewed and approved by the freelancer. */
export function Services({ go }: { go: (t: Tab) => void }) {
  const profile = useApi<ProfileDoc>('/api/profile')
  const doc = useApi<ServicesDoc>('/api/services')
  const build = useJob('/api/services/build', '/api/services/status', () => doc.reload())
  const [editing, setEditing] = useState<ServiceItem[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!profile.data || !doc.data) return <Loading error={profile.error ?? doc.error} />
  if (!profile.data.approved) {
    return (
      <Page title="Services" lede="Sellable, fixed-scope services built from your approved profile.">
        <Empty title="Approve your profile first"
               action={<button className="btn btn-primary" onClick={() => go('onboarding')}>Go to your profile</button>}>
          Chipi designs your services from the skills and proof in your approved profile.
        </Empty>
      </Page>
    )
  }

  const v = doc.data.latest
  const approved = v?.status === 'approved'

  async function save() {
    if (!editing) return
    setBusy(true)
    setError(null)
    try {
      await api.put('/api/services', { services: editing })
      doc.reload()
      setEditing(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function approve() {
    if (!v) return
    setBusy(true)
    setError(null)
    try { await api.post('/api/services/approve', { version: v.version }); doc.reload() }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }

  return (
    <Page title="Services" lede="3 to 5 independent, fixed-scope services, ranked by hiring demand in Dutch job postings.">
      <div className="row-actions" style={{ alignSelf: 'flex-start' }}>
        <button className="btn" disabled={build.running || busy || !!editing} onClick={() => build.start()}>
          {v ? 'Design my services again' : 'Design my services'}
        </button>
        {v && !editing && (
          <button className="btn" disabled={busy || build.running}
            onClick={() => { setError(null); setEditing(v.data.services) }}>Edit services</button>
        )}
        {v && (
          <span className={approved ? 'badge badge-ok' : 'badge'}>
            {approved ? `Approved · version ${v.version}` : `Draft · version ${v.version}`}
          </span>
        )}
      </div>
      <JobStatus job={build.job} startError={build.startError} />

      {editing ? (
        <form className="page" onSubmit={(event) => { event.preventDefault(); void save() }}>
          {editing.map((service, index) => (
            <ServiceEditor key={service.id} service={service}
              skills={profile.data!.approved!.data.skills.map((skill) => skill.name)}
              onChange={(next) => setEditing(editing.map((item, i) => i === index ? next : item))} />
          ))}
          {error && <p className="error" role="alert">{error}</p>}
          <div className="actions">
            <button className="btn btn-primary" disabled={busy}>Save draft</button>
            <button className="btn" type="button" disabled={busy} onClick={() => { setEditing(null); setError(null) }}>
              Cancel
            </button>
          </div>
        </form>
      ) : !v ? (
        <Empty title="No services yet">Chipi reads hiring demand for your skills and designs services you can sell as projects.</Empty>
      ) : (
        <>
          {v.data.services.map((s) => (
            <div key={s.id} className="card service">
              <div className="interview-head">
                <h2>{s.name}</h2>
                <span className="big-num">€{s.rate_range_eur.min}–{s.rate_range_eur.max}<span className="muted small"> /hour</span></span>
              </div>
              <p><b>For:</b> {s.target_buyer}</p>
              <p><b>Problem:</b> {s.problem}</p>
              <p><b>Deliverable:</b> {s.deliverable} <span className="muted">· {s.duration_weeks} weeks</span></p>
              <div className="chips">
                {s.skills_used.map((k) => <span key={k} className="chip">{k}</span>)}
                <span className={`chip strength-${s.proof_strength}`}>{STRENGTH[s.proof_strength]}</span>
              </div>
              <div className="demand">
                <div className="demand-label">
                  <b>Hiring demand</b>
                  <span>{s.demand.value == null ? 'no data yet' : s.demand_index.toFixed(2)}</span>
                </div>
                <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={s.demand_index}
                     aria-label="Hiring demand index">
                  <span style={{ width: `${Math.round((s.demand_index || 0) * 100)}%` }} />
                </div>
                <p className="muted small">{s.demand.explanation}</p>
              </div>
              <p className="small"><b>Why it’s independent work:</b> {s.independence_rationale}</p>
              <p className="small"><b>Why it sells:</b> {s.rationale}</p>
              <p className="small muted">
                Finds leads with: <b>NL</b> {s.discovery_terms.nl.join(', ')} · <b>EN</b> {s.discovery_terms.en.join(', ')}
              </p>
            </div>
          ))}
          {v.checks.length > 0 && <div className="card"><Checks checks={v.checks.map((c) => ({ ...c, note: c.note ?? undefined }))} /></div>}
          {error && <p className="error">{error}</p>}
          {!approved && (
            <div>
              <button className="btn btn-primary" disabled={busy || build.running} onClick={approve}>
                Approve these services
              </button>
            </div>
          )}
          {approved && <p className="muted small">Approved. Scout now searches with these services’ Dutch and English terms.</p>}
          <p className="muted small">Wet DBA framing is guidance only, not legal advice.</p>
        </>
      )}
    </Page>
  )
}

const textFields = [
  ['name', 'Name'], ['target_buyer', 'Target buyer'], ['problem', 'Problem'],
  ['deliverable', 'Deliverable'], ['independence_rationale', 'Why it’s independent work'], ['rationale', 'Why it sells'],
] as const

function ServiceEditor({ service, skills, onChange }: {
  service: ServiceItem
  skills: string[]
  onChange: (service: ServiceItem) => void
}) {
  return (
    <fieldset className="card service-editor">
      <legend>{service.name || 'Service'}</legend>
      {textFields.map(([field, label]) => (
        <label key={field}>{label}
          <input required value={service[field]} onChange={(event) => onChange({ ...service, [field]: event.target.value })} />
        </label>
      ))}
      <label>Duration in weeks
        <input type="number" min={1} max={26} step="any" required value={service.duration_weeks}
          onChange={(event) => onChange({ ...service, duration_weeks: Number(event.target.value) })} />
      </label>
      {(['min', 'max'] as const).map((bound) => (
        <label key={bound}>{bound === 'min' ? 'Minimum' : 'Maximum'} hourly rate (€)
          <input type="number" required min={bound === 'max' ? service.rate_range_eur.min : 1} step="any"
            value={service.rate_range_eur[bound]} onChange={(event) => onChange({
              ...service, rate_range_eur: { ...service.rate_range_eur, [bound]: Number(event.target.value) },
            })} />
        </label>
      ))}
      <label>Skills from your approved profile
        <select multiple required value={service.skills_used}
          onChange={(event) => onChange({
            ...service, skills_used: Array.from(event.target.selectedOptions, (option) => option.value),
          })}>
          {skills.map((skill) => <option key={skill}>{skill}</option>)}
        </select>
      </label>
      {(['nl', 'en'] as const).map((lang) => (
        <label key={lang}>{lang === 'nl' ? 'Dutch' : 'English'} discovery terms, one per line
          <textarea rows={4} defaultValue={service.discovery_terms[lang].join('\n')}
            onChange={(event) => onChange({
              ...service, discovery_terms: { ...service.discovery_terms,
                [lang]: event.target.value.split('\n').map((term) => term.trim()).filter(Boolean) },
            })} />
        </label>
      ))}
    </fieldset>
  )
}
