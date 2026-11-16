import { useEffect, useState } from 'react'
import { useApi } from './api'
import { Chat } from './Chat'
import { ModeContext, type Mode } from './mode'
import { PasscodeGate } from './Passcode'
import type { State } from './types'
import { Today } from './screens/Today'
import { Onboarding } from './screens/Onboarding'
import { FitRate } from './screens/FitRate'
import { Offers } from './screens/Offers'
import { Leads } from './screens/Leads'
import { Drafts } from './screens/Drafts'
import { PipelineView } from './screens/Pipeline'
import { MetricsView } from './screens/Metrics'

// Every screen keeps its own address (#leads, #drafts, …) so links between screens and
// old bookmarks still work. The main navigation groups them into four sections; a section
// with more than one screen shows one row of tabs inside the content to switch between them.
const SECTIONS = [
  ['today', 'Today'],
  ['leads', 'Leads'],
  ['drafts', 'Drafts'],
  ['pipeline', 'Board'],
  ['metrics', 'Metrics'],
  ['onboarding', 'Your profile'],
  ['fit', 'Fit & rate'],
  ['offers', 'Offers'],
] as const
export type Tab = (typeof SECTIONS)[number][0]

// `icon` is a Material Symbols name; index.html loads only these four glyphs.
type Group = { label: string; icon: string; sections: Tab[] }
const GROUPS: Group[] = [
  { label: 'Today', icon: 'today', sections: ['today'] },
  { label: 'Find work', icon: 'work', sections: ['leads', 'drafts'] },
  { label: 'Status', icon: 'view_kanban', sections: ['pipeline', 'metrics'] },
  { label: 'Profile', icon: 'person', sections: ['onboarding', 'fit', 'offers'] }, // set-up, left to right
]

const isTab = (s: string): s is Tab => SECTIONS.some(([id]) => id === s)
const sectionLabel = (t: Tab) => SECTIONS.find(([id]) => id === t)![1]
const groupOf = (t: Tab) => GROUPS.find((g) => g.sections.includes(t))!

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
  const group = groupOf(tab)
  const [chatOpen, setChatOpen] = useState(false)
  const state = useApi<State>('/api/state')
  const mode: Mode = state.data ? (state.data.mode === 'real' ? 'real' : 'mock') : 'unknown'

  return (
    <ModeContext.Provider value={mode}>
    <div className="shell">
      <header className="topbar">
        <a className="wordmark" href="/" aria-label="Chipi home"><span className="dot" aria-hidden="true" />Chipi</a>
        {mode === 'mock' && <span className="badge" title="The backend is serving sample data">Sample data</span>}
        <button className="btn btn-secondary" onClick={() => setChatOpen((o) => !o)}>
          {chatOpen ? 'Close chat' : 'Ask Chipi'}
        </button>
      </header>

      <PasscodeGate />

      <div className="frame">
        {/* Main sections: a bottom bar on phones, a side rail from 840px up (Material 3). */}
        <nav className="appnav" aria-label="Main">
          {GROUPS.map((g) => {
            const on = g === group
            return (
              <a key={g.label} href={`#${g.sections[0]}`} className={on ? 'appnav-item appnav-on' : 'appnav-item'} aria-current={on ? 'true' : undefined}>
                <span className="appnav-icon material-symbols-rounded" aria-hidden="true">{g.icon}</span>
                <span className="appnav-label">{g.label}</span>
              </a>
            )
          })}
        </nav>

        <div className={chatOpen ? 'layout layout-chat' : 'layout'}>
          <main className="main">
            {/* One row of tabs, inside the content, only where a section has more than one view. */}
            {group.sections.length > 1 && (
              <nav className="viewtabs" aria-label={group.label}>
                {group.sections.map((id) => (
                  <a key={id} href={`#${id}`} className={id === tab ? 'viewtab viewtab-on' : 'viewtab'} aria-current={id === tab ? 'page' : undefined}>
                    {sectionLabel(id)}
                  </a>
                ))}
              </nav>
            )}
          {tab === 'today' && <Today go={go} />}
          {tab === 'onboarding' && <Onboarding go={go} />}
          {tab === 'fit' && <FitRate go={go} />}
          {tab === 'offers' && <Offers go={go} />}
          {tab === 'leads' && <Leads go={go} />}
          {tab === 'drafts' && <Drafts go={go} />}
          {tab === 'pipeline' && <PipelineView go={go} />}
          {tab === 'metrics' && <MetricsView />}
          </main>
          {chatOpen && <Chat />}
        </div>
      </div>
    </div>
    </ModeContext.Provider>
  )
}
