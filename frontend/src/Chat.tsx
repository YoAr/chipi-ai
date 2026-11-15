import { useRef, useState, type FormEvent } from 'react'
import { streamChat } from './api'
import { Markdown } from './Markdown'

type Msg = { role: 'user' | 'assistant'; content: string }

export function Chat() {
  const [messages, setMessages] = useState<Msg[]>([
    { role: 'assistant', content: 'Hi! Ask me what to charge, which leads to chase, or how a draft could be better.' },
  ])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [tool, setTool] = useState<string | null>(null)
  const endRef = useRef<HTMLDivElement>(null)

  async function send(e: FormEvent) {
    e.preventDefault()
    const text = input.trim()
    if (!text || busy) return
    const history: Msg[] = [...messages, { role: 'user', content: text }]
    setMessages([...history, { role: 'assistant', content: '' }])
    setInput('')
    setBusy(true)
    const append = (delta: string) =>
      setMessages((m) => {
        const copy = m.slice()
        copy[copy.length - 1] = { role: 'assistant', content: copy[copy.length - 1].content + delta }
        return copy
      })
    // Skip the canned greeting: the API only needs the real conversation.
    await streamChat(history.slice(1), (ev) => {
      if (ev.type === 'text') append(ev.delta)
      if (ev.type === 'tool') setTool(ev.status === 'running' ? ev.name : null)
      if (ev.type === 'error') append(`\n\n(${ev.message})`)
      endRef.current?.scrollIntoView({ block: 'end' })
    }).catch((err: Error) => append(`\n\n(Chat failed: ${err.message})`))
    setTool(null)
    setBusy(false)
  }

  return (
    <aside className="chat" aria-label="Chat with Chipi">
      <div className="chat-log">
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'msg msg-user' : 'msg msg-md'}>
            {m.role === 'user'
              ? m.content
              : m.content
                ? <Markdown text={m.content} />
                : busy && i === messages.length - 1 ? '…' : ''}
          </div>
        ))}
        {tool && <p className="muted small">Checking {tool.replace(/_/g, ' ')}…</p>}
        <div ref={endRef} />
      </div>
      <form className="chat-form" onSubmit={send}>
        <label htmlFor="chat-input" className="sr-only">Message</label>
        <input id="chat-input" value={input} onChange={(e) => setInput(e.target.value)} placeholder="What should I charge?" />
        <button className="btn btn-primary" disabled={busy || !input.trim()}>Send</button>
      </form>
    </aside>
  )
}
