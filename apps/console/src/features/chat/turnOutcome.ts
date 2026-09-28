import type { ChatTurnOutcome } from '../../shared/data/api'

export type TurnEndCursor = { stream_epoch?: string; stream_turn?: number } | null

const sentences: Record<ChatTurnOutcome, string> = {
  complete: 'Response complete.',
  stopped: 'Response stopped.',
  error: 'Response ended with an error.',
}

export function readTurnOutcome(value: unknown, superseded = false): ChatTurnOutcome | null {
  if (superseded) return 'stopped'
  return value === 'complete' || value === 'stopped' || value === 'error' ? value : null
}

export function claimTurnEndAnnouncement(
  announced: Set<string>,
  session: string,
  cursor: TurnEndCursor,
  outcome: ChatTurnOutcome | null,
): { sentence: string; cue: 'turn_complete' | 'error' } | null {
  if (!outcome || !session) return null
  const turnKey = cursor?.stream_epoch && typeof cursor.stream_turn === 'number'
    ? `${session}:${cursor.stream_epoch}:${cursor.stream_turn}`
    : `${session}:terminal`
  if (announced.has(turnKey)) return null
  announced.add(turnKey)
  if (announced.size > 128) announced.delete(announced.values().next().value as string)
  return { sentence: sentences[outcome], cue: outcome === 'error' ? 'error' : 'turn_complete' }
}

export function acceptedActionSnapshot<T>(response: { ok?: boolean; snapshot?: T | null }): T {
  if (response.ok !== true || response.snapshot == null) {
    throw new Error('The server did not accept this chat change.')
  }
  return response.snapshot
}
