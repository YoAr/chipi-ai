import { useEffect, useState } from 'react'
import type { ChatEvent } from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  const body = await res.json().catch(() => null)
  if (!res.ok) throw new Error(body?.error?.message ?? `Request failed (${res.status})`)
  return body as T
}

const json = (method: string, data: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(data),
})

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, data: unknown) => request<T>(path, json('POST', data)),
  put: <T>(path: string, data: unknown) => request<T>(path, json('PUT', data)),
  upload: <T>(path: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<T>(path, { method: 'POST', body: form })
  },
}

/** Load a GET route; call reload() after a write to refresh it. */
export function useApi<T>(path: string) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let live = true
    api
      .get<T>(path)
      .then((d) => live && (setData(d), setError(null)))
      .catch((e: Error) => live && setError(e.message))
    return () => {
      live = false
    }
  }, [path, tick])

  return { data, error, reload: () => setTick((t) => t + 1) }
}

/** POST /api/chat and call onEvent for each server-sent event. */
export async function streamChat(
  messages: { role: 'user' | 'assistant'; content: string }[],
  onEvent: (e: ChatEvent) => void,
): Promise<void> {
  const res = await fetch('/api/chat', json('POST', { messages }))
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => null)
    onEvent({ type: 'error', message: body?.error?.message ?? `Chat failed (${res.status})` })
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const line = chunk.split('\n').find((l) => l.startsWith('data: '))
      if (line) onEvent(JSON.parse(line.slice(6)) as ChatEvent)
    }
  }
}
