import { turnText, type ChatTurn } from './chatTypes'

export interface SessionMapEntry {
  /** Position in the question-only marker rail. */
  index: number
  /** Transcript turn that opens this exchange. */
  turnIndex: number
  /** Last transcript turn owned by this question, inclusive. */
  endIndex: number
  turn: SearchTurn
}

export interface SessionMapResult { index: number; entryIndex: number; text: string; source: string }

type SearchTurn = Pick<ChatTurn, 'role'> & Partial<Pick<ChatTurn, 'segments' | 'summary' | 'ts'>>

/** One marker per user question, owning every following turn up to the next question. */
export function sessionMapEntries(turns: readonly SearchTurn[]): SessionMapEntry[] {
  const entries: SessionMapEntry[] = []
  turns.forEach((turn, turnIndex) => {
    if (turn.role === 'user') {
      entries.push({ index: entries.length, turnIndex, endIndex: turnIndex, turn })
    } else if (entries.length) {
      entries[entries.length - 1].endIndex = turnIndex
    }
  })
  return entries
}

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
  const entries = sessionMapEntries(turns)
  const entryForTurn = new Map<number, number>()
  entries.forEach((entry) => {
    for (let index = entry.turnIndex; index <= entry.endIndex; index++) entryForTurn.set(index, entry.index)
  })
  const results: SessionMapResult[] = []
  turns.forEach((turn, index) => {
    const entryIndex = entryForTurn.get(index)
    if (entryIndex === undefined) return
    const text = turn.segments ? turnText({ role: turn.role, segments: turn.segments }) : ''
    if (text.toLocaleLowerCase().includes(needle)) {
      results.push({ index, entryIndex, text, source: turn.role === 'user' ? 'Your message' : 'Agent response' })
      return
    }
    for (const segment of turn.segments ?? []) {
      if (segment.kind !== 'tool') continue
      const output = [segment.tool, segment.detail, segment.output].filter(Boolean).join(' · ')
      if (output.toLocaleLowerCase().includes(needle)) {
        results.push({ index, entryIndex, text: output, source: `Tool: ${segment.tool}` })
        return
      }
    }
  })
  return results
}
