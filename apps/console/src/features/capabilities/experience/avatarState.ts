import type { AgentActivityEntity } from '../../../shared/data/useAgentActivity'
export const avatarStates = ['idle', 'working', 'needs_input', 'waiting_approval', 'error', 'speaking'] as const
export function avatarState(entities: readonly AgentActivityEntity[], entityId: string, playing: boolean, unavailable: boolean) {
  if (playing) return 'speaking'
  if (unavailable) return 'unknown'
  const selected = entityId ? entities.find(entity => entity.id === entityId) : entities[0]
  return selected?.state || (entityId ? 'unknown' : 'idle')
}
export function avatarClip(state: string, clips: Record<string, string>) {
  return state === 'unknown' ? undefined : clips[state] || clips.idle
}
