import { useEffect, useState } from 'react'
import { api, useApi, useJob } from '../api'
import type { ProfileDoc, ServicesDoc } from '../types'
import { JobStatus, Loading, Page } from '../ui'
import './insights.css'

type Segment = { value: string; sent: number; replies: number; positive_replies: number; reply_rate: number; positive_reply_rate: number }
type InsightsData = {
  funnel: { stages: { stage: string; count: number }[] }
  segments: Record<'service_id' | 'channel' | 'hook_type' | 'language', Segment[]>
}
type Observation = { point: string; evidence: string }
type Learning = {
  id: string
  company: string | null
  held_on: string | null
  sentiment: { score: number; trend: string; evidence: string[] }
  working: Observation[]
  not_working: Observation[]
  risks: Observation[]
  skill_proposals: { skill: string; evidence: string; proof_strength: string }[]
  service_proposals: { change: string; rationale: string; evidence: string }[]
  approvals: Record<string, string>
}
type LearningData = {
  engagements: { id: string; title: string; company: string; status: string; has_reviews: boolean }[]
  learnings: Learning[]
}
const dimensions = [['service_id', 'Service'], ['channel', 'Channel'], ['hook_type', 'Hook type'], ['language', 'Language']] as const
const label = (value: string) => value.replace(/_/g, ' ')
const day = () => new Date().toLocaleDateString('sv-SE')

export function Insights() {
  const insights = useApi<InsightsData>('/api/insights')
  const services = useApi<ServicesDoc>('/api/services')
  return (
    <Page title="Insights" lede="Track how outreach performs and review what you’re learning from client meetings.">
      {!insights.data ? <Loading error={insights.error} /> : (
        <>
          <section className="card">
            <h2>Outreach funnel</h2>
            <dl className="metric-grid">
              {insights.data.funnel.stages.map(({ stage, count }) => (
                <div key={stage}><dt>{label(stage)}</dt><dd>{count}</dd></div>
              ))}
            </dl>
          </section>
          {dimensions.map(([dimension, title]) => (
            <section className="card" key={dimension}>
              <h2>Replies by {title.toLowerCase()}</h2>
              {insights.data!.segments[dimension].length === 0 ? <p className="muted">No logged sends yet.</p> : (
                <div className="table-scroll">
                  <table>
                    <thead><tr><th scope="col">{title}</th><th scope="col">Sent</th><th scope="col">Replies</th>
                      <th scope="col">Reply rate</th><th scope="col">Positive reply rate</th></tr></thead>
                    <tbody>{insights.data!.segments[dimension].map((row) => (
                      <tr key={row.value}>
                        <th scope="row">{dimension === 'service_id'
                          ? services.data?.approved?.data.services.find((s) => s.id === row.value)?.name ?? row.value
                          : label(row.value)}</th>
                        <td>{row.sent}</td><td>{row.replies}</td>
                        <td>{Math.round(row.reply_rate * 100)}%</td>
                        <td>{Math.round(row.positive_reply_rate * 100)}% ({row.positive_replies})</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              )}
            </section>
          ))}
        </>
      )}
      <MeetingReviews />
    </Page>
  )
}

function MeetingReviews() {
  const reviews = useApi<LearningData>('/api/learnings')
  const job = useJob('/api/meetings', '/api/learnings/status', (completedJob) => {
    if (completedJob.result?.meeting_id) {
      setTranscript('')
      setConsent(false)
    }
    reviews.reload()
  })
  const [lead, setLead] = useState('')
  const [heldOn, setHeldOn] = useState(day)
  const [transcript, setTranscript] = useState('')
  const [consent, setConsent] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [approval, setApproval] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const busy = submitting || job.running
  const reload = reviews.reload

  useEffect(() => {
    window.addEventListener('chipi:passcode-unlocked', reload)
    return () => window.removeEventListener('chipi:passcode-unlocked', reload)
  }, [reload])

  async function approve(learningId: string, kind: 'skill' | 'service', index: number) {
    setApproval(learningId + kind + index)
    setError(null)
    try {
      await api.post(`/api/learnings/${learningId}/approve`, { kind, index })
      reviews.reload()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setApproval(null)
    }
  }

  async function addSkillToProfile(skill: string) {
    setApproval('profile')
    setError(null)
    setNotice(null)
    try {
      const { latest } = await api.get<ProfileDoc>('/api/profile')
      if (!latest) throw new Error('Create your profile before adding skills.')
      const data = latest.data
      if (data.skills.some(({ name }) => name.trim().toLowerCase() === skill.trim().toLowerCase())) {
        setNotice('This skill is already in your latest profile.')
        return
      }
      await api.put('/api/profile', { ...data, skills: [
        ...data.skills, { name: skill, taxonomy_id: null, evidence: [], proof_strength: 'claimed' },
      ] })
      setNotice('Skill added to your profile draft. Review and approve it in Your profile.')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setApproval(null)
    }
  }

  async function start(isSummary: boolean) {
    setSubmitting(true)
    try {
      if (isSummary) await job.start({ lead_id: lead }, '/api/learnings/engagement')
      else await job.start({ lead_id: lead, held_on: heldOn, transcript, consent_confirmed: consent })
    } finally {
      setSubmitting(false)
    }
  }

  if (!reviews.data) return <Loading error={reviews.error} />
  const engagement = reviews.data.engagements.find((item) => item.id === lead)
  const canUpload = engagement?.status === 'meeting' || engagement?.status === 'won'
  return (
    <>
      <section className="card">
        <h2>Meeting transcripts</h2>
        {reviews.data.engagements.length === 0 ? (
          <p className="muted">Log a meeting or a win from the Board to add a transcript.</p>
        ) : (
          <form className="page insight-fields" onSubmit={(event) => { event.preventDefault(); void start(false) }}>
            <label>Engagement
              <select disabled={busy} required value={lead} onChange={(event) => setLead(event.target.value)}>
                <option value="">Choose an engagement</option>
                {reviews.data.engagements.map((item) => (
                  <option key={item.id} value={item.id}>{item.company} · {item.title}</option>
                ))}
              </select>
            </label>
            {canUpload && <>
              <label>Meeting date
                <input type="date" disabled={busy} required value={heldOn} onChange={(event) => setHeldOn(event.target.value)} />
              </label>
              <label>Transcript
                <textarea rows={7} disabled={busy} required maxLength={100000} value={transcript}
                  onChange={(event) => setTranscript(event.target.value)} />
              </label>
              <label className="consent">
                <input type="checkbox" disabled={busy} checked={consent} onChange={(event) => setConsent(event.target.checked)} />
                {' '}Everyone in the meeting consented to sharing this transcript for AI analysis.
              </label>
            </>}
            <div className="actions">
              {canUpload && <button className="btn btn-primary" disabled={busy || !consent || !transcript.trim()}>
                Analyze transcript
              </button>}
              {engagement?.has_reviews && (
                <button type="button" className="btn" disabled={busy} onClick={() => void start(true)}>
                  Summarize completed engagement
                </button>
              )}
            </div>
          </form>
        )}
        <JobStatus job={job.job} startError={job.startError} />
      </section>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {reviews.data.learnings.map((review) => (
        <section className="card" key={review.id}>
          <h2>{review.company || 'Client'} · {review.held_on || 'Engagement summary'}</h2>
          <p>Sentiment {review.sentiment.score > 0 ? '+' : ''}{review.sentiment.score} · Trend {review.sentiment.trend}</p>
          <ul>{review.sentiment.evidence.map((quote, i) => <li key={i}><q>{quote}</q></li>)}</ul>
          {([
            ['What worked', review.working], ['What didn’t work', review.not_working], ['Risks', review.risks],
          ] as const).map(([title, items]) => items.length > 0 && (
            <div key={title}><h3>{title}</h3>
              <ul>{items.map(({ point, evidence }, i) => <li key={i}>{point} <q>{evidence}</q></li>)}</ul>
            </div>
          ))}
          {(['skill', 'service'] as const).map((kind) => {
            const proposals = kind === 'skill' ? review.skill_proposals : review.service_proposals
            return proposals.length > 0 && (
              <div key={kind}>
                <h3>{kind === 'skill' ? 'Skill proposals' : 'Service proposals'}</h3>
                {proposals.map((item, i) => (
                  <div key={i} className="callout">
                    <div>
                      <strong>{'skill' in item ? item.skill : item.change}</strong>
                      <p>{'skill' in item ? label(item.proof_strength) : item.rationale}</p>
                      <q>{item.evidence}</q>
                    </div>
                    {review.approvals[`${kind}:${i}`] ? (
                      <div className="actions">
                        <span className="badge badge-ok">Approved</span>
                        {'skill' in item && (
                          <button className="btn" disabled={approval !== null} onClick={() => void addSkillToProfile(item.skill)}>
                            Add to profile draft
                          </button>
                        )}
                      </div>
                    ) : (
                      <button className="btn" disabled={approval !== null} onClick={() => void approve(review.id, kind, i)}>
                        Approve proposal
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )
          })}
        </section>
      ))}
    </>
  )
}
