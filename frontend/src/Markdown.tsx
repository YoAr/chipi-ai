import { Fragment, type ReactNode } from 'react'

/**
 * A small, safe Markdown subset for chat replies: paragraphs, "-"/"1." lists,
 * **bold**, *italic* and `code`. Headings become plain paragraphs.
 *
 * It builds React elements (never HTML strings), so model output can't inject markup.
 * Unfinished markers while a reply is still streaming (e.g. "**12") just show as text
 * until the closing marker arrives.
 */
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g

function inline(text: string, key: string): ReactNode[] {
  return text
    .split(INLINE)
    .filter(Boolean)
    .map((part, i) => {
      const k = `${key}-${i}`
      if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) return <strong key={k}>{part.slice(2, -2)}</strong>
      if (part.length > 2 && part.startsWith('`') && part.endsWith('`')) return <code key={k}>{part.slice(1, -1)}</code>
      if (part.length > 2 && part.startsWith('*') && part.endsWith('*')) return <em key={k}>{part.slice(1, -1)}</em>
      return <Fragment key={k}>{part}</Fragment>
    })
}

type List = { ordered: boolean; items: string[] }

export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  let para: string[] = []
  let list: List | null = null

  const flushPara = () => {
    if (!para.length) return
    const k = `p${blocks.length}`
    blocks.push(<p key={k}>{inline(para.join(' '), k)}</p>)
    para = []
  }
  const flushList = () => {
    if (!list) return
    const k = `l${blocks.length}`
    const items = list.items.map((item, i) => <li key={i}>{inline(item, `${k}-${i}`)}</li>)
    blocks.push(list.ordered ? <ol key={k}>{items}</ol> : <ul key={k}>{items}</ul>)
    list = null
  }

  for (const raw of text.split('\n')) {
    const line = raw.trimEnd()
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line)
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line)
    const item = bullet ?? numbered
    if (item) {
      flushPara()
      const ordered = numbered !== null && bullet === null
      if (!list || list.ordered !== ordered) {
        flushList()
        list = { ordered, items: [] }
      }
      list.items.push(item[1])
    } else if (line.trim() === '') {
      flushPara()
      flushList()
    } else {
      flushList()
      para.push(line.replace(/^#{1,6}\s+/, ''))
    }
  }
  flushPara()
  flushList()
  return <>{blocks}</>
}
