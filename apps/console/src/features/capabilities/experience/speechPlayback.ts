import { useSyncExternalStore } from 'react'
const playing = new Set<string>()
const listeners = new Set<() => void>()
export function reportSpeechPlayback(ownerId: string, active: boolean) {
  const before = playing.size > 0
  if (active) playing.add(ownerId)
  else playing.delete(ownerId)
  if (before !== (playing.size > 0)) for (const listener of listeners) listener()
}
export const speechPlaybackActive = () => playing.size > 0
export function useSpeechPlayback() {
  return useSyncExternalStore(listener => { listeners.add(listener); return () => { listeners.delete(listener) } }, speechPlaybackActive, () => false)
}
