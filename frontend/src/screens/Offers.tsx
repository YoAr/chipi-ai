import { useApi } from '../api'
import type { Tab } from '../App'
import type { Offer } from '../types'
import { Empty, Loading, Page } from '../ui'

export function Offers({ go }: { go: (t: Tab) => void }) {
  const { data, error } = useApi<{ offers: Offer[] }>('/api/offers')
  if (!data) return <Loading error={error} />
  return (
    <Page title="Offers" lede="Packages you could sell, shaped around your experience.">
      {data.offers.length === 0 ? (
        <Empty title="No offers yet"
               action={<button className="btn btn-primary" onClick={() => go('onboarding')}>Go to your profile</button>}>
          Chipi suggests three offers once it knows your experience and your rate.
        </Empty>
      ) : (
        <div className="grid3">
          {data.offers.map((o) => (
            <div key={o.id} className="card">
              <h2>{o.name}</h2>
              <p>{o.pitch}</p>
              <p className="big">{o.price}</p>
              <p className="muted small">Demand: {o.evidence}</p>
              <p className="muted small">Fit: {o.fit}</p>
            </div>
          ))}
        </div>
      )}
    </Page>
  )
}
