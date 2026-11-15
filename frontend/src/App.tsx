import { useEffect, useState } from 'react'
import { Chat } from './Chat'
import { Today } from './screens/Today'
import { Onboarding } from './screens/Onboarding'
import { FitRate } from './screens/FitRate'
import { Offers } from './screens/Offers'
import { Leads } from './screens/Leads'
import { Drafts } from './screens/Drafts'
import { PipelineView } from './screens/Pipeline'
import { MetricsView } from './screens/Metrics'

const TABS = [
  ['today', 'Today'],
  ['onboarding', 'Your profile'],
  ['fit', 'Fit & rate'],
  ['offers', 'Offers'],
  ['leads', 'Leads'],
  ['drafts', 'Drafts'],
  ['pipeline', 'Pipeline'],
  ['metrics', 'Metrics'],
] as const
export type Tab = (typeof TABS)[number][0]

const isTab = (s: string): s is Tab => TABS.some(([id]) => id === s)

function useHashTab(): [Tab, (t: Tab) => void] {
  const read = (): Tab => {
    const h = window.location.hash.slice(1)
    return isTab(h) ? h : 'today'
  }
  const [tab, setTab] = useState<Tab>(read)
  useEffect(() => {
    const onHash = () => setTab(read())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return [tab, (t) => (window.location.hash = t)]
}

export default function App() {
  const [tab, go] = useHashTab()
  const [chatOpen, setChatOpen] = useState(false)

  return (
    <div className="shell">
      <header className="topbar">
        <a className="wordmark" href="/">Chipi</a>
        <span className="badge" title="The backend is serving sample data">Sample data</span>
        <button className="btn btn-primary" onClick={() => setChatOpen((o) => !o)}>
          {chatOpen ? 'Close chat' : 'Ask Chipi'}
        </button>
      </header>

      <nav className="tabs" aria-label="Sections">
        {TABS.map(([id, label]) => (
          <a key={id} href={`#${id}`} className={id === tab ? 'tab tab-on' : 'tab'} aria-current={id === tab ? 'page' : undefined}>
            {label}
          </a>
        ))}
      </nav>

      <div className={chatOpen ? 'layout layout-chat' : 'layout'}>
        <main className="main">
          {tab === 'today' && <Today go={go} />}
          {tab === 'onboarding' && <Onboarding go={go} />}
          {tab === 'fit' && <FitRate />}
          {tab === 'offers' && <Offers />}
          {tab === 'leads' && <Leads />}
          {tab === 'drafts' && <Drafts />}
          {tab === 'pipeline' && <PipelineView />}
          {tab === 'metrics' && <MetricsView />}
        </main>
        {chatOpen && <Chat />}
      </div>
    </div>
  )
}
