import { useApi } from '../api'
import type { Tab } from '../App'
import type { Draft, Lead, State } from '../types'
import { Loading, Page, sourceLabel } from '../ui'

export function Today({ go }: { go: (t: Tab) => void }) {
  const state = useApi<State>('/api/state')
  const leads = useApi<{ leads: Lead[] }>('/api/leads?status=new')
  const drafts = useApi<{ drafts: Draft[] }>('/api/drafts?status=pending')

  if (!state.data) return <Loading error={state.error} />
  const { profile, today } = state.data
  const top = [...(leads.data?.leads ?? [])].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)).slice(0, 3)
  const noProfile = !profile.name && !profile.headline

  return (
    <Page title={profile.name ? `Good morning, ${profile.name}` : 'Good morning'}
          lede={profile.headline || 'Start with your CV. Chipi works out what you could sell and what to charge.'}>
      {noProfile && (
        <div className="callout">
          <span>No profile yet. It takes about 5 minutes.</span>
          <button className="btn btn-primary" onClick={() => go('onboarding')}>Upload your CV</button>
        </div>
      )}
      <div className="stats">
        <button className="stat" onClick={() => go('leads')}><b>{today.new_leads}</b>new leads</button>
        <button className="stat" onClick={() => go('drafts')}><b>{today.drafts_pending}</b>drafts to approve</button>
        <button className="stat" onClick={() => go('pipeline')}><b>{today.deadlines_this_week}</b>deadline this week</button>
        <button className="stat" onClick={() => go('pipeline')}><b>{today.replies_unread}</b>new reply</button>
      </div>

      <h2>Top leads today</h2>
      <div className="list">
        {top.map((l) => (
          <div key={l.id} className="row">
            <span className="score">{l.score ?? '—'}</span>
            <div className="grow">
              <b>{l.title}</b>
              <p className="muted small">{sourceLabel(l.source)} · {l.rate_text ?? 'rate unknown'}</p>
            </div>
          </div>
        ))}
        {top.length === 0 && (
          <p className="muted">
            No new leads yet. <button className="link-btn" onClick={() => go('leads')}>Find leads</button>
          </p>
        )}
      </div>

      {(drafts.data?.drafts.length ?? 0) > 0 && (
        <div className="callout">
          <span>{drafts.data!.drafts.length} draft waiting for your approval.</span>
          <button className="btn btn-primary" onClick={() => go('drafts')}>Review drafts</button>
        </div>
      )}
    </Page>
  )
}
