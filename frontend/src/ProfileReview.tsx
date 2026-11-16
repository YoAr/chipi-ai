import { useState } from 'react'
import { api } from './api'
import type { ProfileData, ProfileDoc, ProfileVersion } from './types'
import { Checks } from './ui'

const STRENGTH: Record<string, string> = { claimed: 'Claimed', demonstrated: 'Demonstrated', referenceable: 'Referenceable' }
const list = (items: string[]) => items.join(', ')
const split = (text: string) => text.split(',').map((s) => s.trim()).filter(Boolean)

/** Spec v2: the freelancer reviews, edits and approves the profile (a human gate). */
export function ProfileReview({ doc, onChange }: { doc: ProfileDoc; onChange: () => void }) {
  const v = doc.latest as ProfileVersion
  const d = v.data
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [edit, setEdit] = useState({
    rate: d.rate_floor_eur_hour?.toString() ?? '',
    hours: d.availability_hours_week?.toString() ?? '',
    preferred: list(d.preferred_work),
    exclusions: list(d.exclusions),
  })
  const dirty = edit.rate !== (d.rate_floor_eur_hour?.toString() ?? '') || edit.hours !== (d.availability_hours_week?.toString() ?? '')
    || edit.preferred !== list(d.preferred_work) || edit.exclusions !== list(d.exclusions)

  async function run(fn: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try { await fn(); onChange() } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const save = () => run(() => {
    const num = (s: string) => (s.trim() === '' ? null : Number(s))
    const data: ProfileData = { ...d, rate_floor_eur_hour: num(edit.rate), availability_hours_week: num(edit.hours),
      preferred_work: split(edit.preferred), exclusions: split(edit.exclusions) }
    return api.put('/api/profile', data)
  })
  const approve = () => run(() => api.post('/api/profile/approve', { version: v.version }))
  const isApproved = v.status === 'approved'

  return (
    <div className="card">
      <div className="interview-head">
        <h2>Your profile</h2>
        <span className={isApproved ? 'badge badge-ok' : 'badge'}>
          {isApproved ? `Approved · version ${v.version}` : `Draft · version ${v.version}`}
        </span>
      </div>
      <p className="muted small">
        Built only from your CV and your answers. Anything Chipi couldn’t find there was removed and is listed below.
      </p>

      <h3>Skills and proof</h3>
      <ul className="skills">
        {d.skills.map((s) => (
          <li key={s.name}>
            <b>{s.name}</b> <span className={`chip strength-${s.proof_strength}`}>{STRENGTH[s.proof_strength]}</span>
            {s.evidence.length > 0 && (
              <ul className="muted small">
                {s.evidence.map((e, i) => (
                  <li key={i}>
                    {e.claim}
                    {e.metric && <> · <b>{e.metric}</b></>}
                    {e.client && <> · {e.client}{e.citable ? ' (may be named)' : ' (not to be named)'}</>}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
      {d.skill_combinations.length > 0 && (
        <p><b>Skill combinations:</b> {d.skill_combinations.map((c) => c.skills.join(' + ')).join(' · ')}</p>
      )}
      {d.industries.length > 0 && <p><b>Industries:</b> {list(d.industries)}</p>}
      {d.past_employers.length > 0 && <p><b>Past employers:</b> {list(d.past_employers)}</p>}
      {d.schools.length > 0 && <p><b>Schools:</b> {list(d.schools)}</p>}
      <p><b>Languages:</b> {d.languages.map((l) => (l === 'nl' ? 'Dutch' : 'English')).join(', ') || '—'}</p>
      {d.voice_samples.length > 0 && <p className="muted small">{d.voice_samples.length} writing sample(s) saved for your voice.</p>}

      <div className="fields">
        <label>
          Minimum rate (€ / hour)
          <input type="number" min={1} step={5} value={edit.rate} disabled={busy} onChange={(e) => setEdit({ ...edit, rate: e.target.value })} />
        </label>
        <label>
          Hours a week
          <input type="number" min={1} max={80} value={edit.hours} disabled={busy} onChange={(e) => setEdit({ ...edit, hours: e.target.value })} />
        </label>
        <label className="field-wide">
          Work you want (comma-separated)
          <input value={edit.preferred} disabled={busy} onChange={(e) => setEdit({ ...edit, preferred: e.target.value })} />
        </label>
        <label className="field-wide">
          Work you won’t take (comma-separated)
          <input value={edit.exclusions} disabled={busy} onChange={(e) => setEdit({ ...edit, exclusions: e.target.value })} />
        </label>
      </div>

      {v.checks.length > 0 && <Checks checks={v.checks.map((c) => ({ ...c, note: c.note ?? undefined }))} />}
      {error && <p className="error">{error}</p>}
      <div className="row-actions">
        {dirty && <button className="btn" disabled={busy} onClick={save}>Save changes</button>}
        {!isApproved && (
          <button className="btn btn-primary" disabled={busy || dirty || d.rate_floor_eur_hour == null} onClick={approve}>
            Approve my profile
          </button>
        )}
      </div>
      {!isApproved && d.rate_floor_eur_hour == null && <p className="muted small">Add your minimum rate to approve.</p>}
      {isApproved && <p className="muted small">Approved. Your minimum rate of €{d.rate_floor_eur_hour}/hour now guards every price.</p>}
    </div>
  )
}
