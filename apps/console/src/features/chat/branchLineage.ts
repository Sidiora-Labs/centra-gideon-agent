
import type { ChatTurn } from './chatTypes'

export function branchIndexOf(turns: ChatTurn[], i: number): number {
  const turn = turns[i]
  if (!turn) return i
  if (typeof turn.visibleIndex === 'number') return turn.visibleIndex
  for (let j = i - 1; j >= 0; j--) {
    const anchor = turns[j].visibleIndex
    if (typeof anchor !== 'number') continue
    let steps = 0
    for (let k = j + 1; k <= i; k++) if (turnHoldsMessage(turns[k])) steps += 1
    return anchor + steps
  }
  return i
}

function turnHoldsMessage(turn: ChatTurn | undefined): boolean {
  if (!turn) return false
  if (turn.role === 'user') return true
  return turn.segments.some((s) => s.kind === 'text')
}

export function branchParentKey(forkedFrom: string | null | undefined): string {
  const raw = (forkedFrom || '').trim()
  if (!raw) return ''
  if (raw.startsWith('dashboard:')) return raw.slice('dashboard:'.length)
  return raw.replace(/^dashboard_+/, '')
}
