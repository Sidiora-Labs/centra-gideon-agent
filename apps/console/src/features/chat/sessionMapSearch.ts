import { turnText, type ChatTurn } from './chatTypes'

export interface SessionMapResult { index: number; text: string; source: string }

type SearchTurn = Pick<ChatTurn, 'role'> & Partial<Pick<ChatTurn, 'segments' | 'summary'>>

export function turnLabel(turn: SearchTurn): string {
  const text = turn.summary || (turn.segments ? turnText({ role: turn.role, segments: turn.segments }) : '')
  const compact = text.replace(/\s+/g, ' ').replace(/^[#>*\-\s]+/, '').trim()
  if (!compact) return turn.role === 'user' ? 'Your message' : 'Agent response'
  const words = compact.split(' ').slice(0, 7).join(' ')
  return words.length > 58 ? `${words.slice(0, 57).trimEnd()}…` : words
}

export function sessionMapResults(turns: readonly SearchTurn[], query: string): SessionMapResult[] {
  const needle = query.trim().toLocaleLowerCase()
  if (!needle) return []
  const results: SessionMapResult[] = []
  turns.forEach((turn, index) => {
    const text = turn.segments ? turnText({ role: turn.role, segments: turn.segments }) : ''
    if (text.toLocaleLowerCase().includes(needle)) {
      results.push({ index, text, source: turn.role === 'user' ? 'Your message' : 'Agent response' })
      return
    }
    for (const segment of turn.segments ?? []) {
      if (segment.kind !== 'tool') continue
      const output = [segment.tool, segment.detail, segment.output].filter(Boolean).join(' · ')
      if (output.toLocaleLowerCase().includes(needle)) {
        results.push({ index, text: output, source: `Tool: ${segment.tool}` })
        return
      }
    }
  })
  return results
}
