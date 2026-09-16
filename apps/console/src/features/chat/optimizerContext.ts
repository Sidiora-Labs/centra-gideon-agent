
import { turnText, type ChatTurn } from './chatTypes'

export const CTX_MAX_TURNS = 10
export const CTX_TURN_CHARS = 400
const CLIP = '…'
const LABELS: Record<ChatTurn['role'], string> = { user: 'user: ', assistant: 'assistant: ' }
const WIDEST_LABEL = LABELS.assistant.length

export const CTX_BUDGET_CHARS =
  CTX_MAX_TURNS * (CTX_TURN_CHARS + CLIP.length + WIDEST_LABEL) + (CTX_MAX_TURNS - 1)

export function buildOptimizerContext(turns: ChatTurn[]): string {
  const lines: string[] = []
  for (let i = turns.length - 1; i >= 0 && lines.length < CTX_MAX_TURNS; i--) {
    const t = turns[i]
    const body = turnText(t)
    if (!body) continue
    const clipped = body.length > CTX_TURN_CHARS ? body.slice(0, CTX_TURN_CHARS) + CLIP : body
    lines.push(LABELS[t.role] + clipped.replace(/\s*\n\s*/g, ' '))
  }
  return lines.reverse().join('\n')
}
