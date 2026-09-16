
import type { ChatTurn } from './chatTypes'

export function findSegments(turn: ChatTurn): string[] {
  const out: string[] = []
  for (const seg of turn.segments) {
    if (seg.kind === 'text') out.push(seg.text)
    else if (seg.kind === 'tool') out.push([seg.tool, seg.detail].filter(Boolean).join(' '))
    else if (seg.kind === 'activity') out.push(seg.text)
    else if (seg.kind === 'error') out.push(seg.text)
    else out.push('')
  }
  return out
}
