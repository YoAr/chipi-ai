import { useApi } from '../api'
import type { Offer } from '../types'
import { Loading, Page } from '../ui'

export function Offers() {
  const { data, error } = useApi<{ offers: Offer[] }>('/api/offers')
  if (!data) return <Loading error={error} />
  return (
    <Page title="Offers" lede="Packages you could sell, shaped around your experience.">
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
    </Page>
  )
}
