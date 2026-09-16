import { useEffect, useReducer, useRef } from 'react'
import type { OnboardingStatePatch } from '../../shared/data/api'
import { TRY_ONE_FLOWS, failureText, settingsTargetFor, type SettingsTarget, type TryOneId, type TryOneOutcome } from './tryOneFlows'
export type TryCardState = { phase: 'idle' } | { phase: 'running' } | { phase: 'done'; outcome: TryOneOutcome } | { phase: 'failed'; message: string; target: SettingsTarget }
export function useSetupTrials(onProgress: (patch: OnboardingStatePatch) => void) {
  const [states, update] = useReducer((state: Partial<Record<TryOneId, TryCardState>>, change: { id: TryOneId; state: TryCardState }) => ({ ...state, [change.id]: change.state }), {})
  const active = useRef(new Set<TryOneId>())
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const run = async (id: TryOneId) => {
    if (active.current.has(id)) return
    active.current.add(id); update({ id, state: { phase: 'running' } })
    try {
      const outcome = await TRY_ONE_FLOWS[id]()
      if (mounted.current) update({ id, state: { phase: 'done', outcome } })
      onProgress({ first_success: { [id]: true } })
    } catch (error) {
      const message = failureText(error)
      if (mounted.current) update({ id, state: { phase: 'failed', message, target: settingsTargetFor(message, (error as { status?: number } | null)?.status) } })
    } finally { active.current.delete(id) }
  }
  return { run, stateOf: (id: TryOneId): TryCardState => states[id] ?? { phase: 'idle' }, doneCount: Object.values(states).filter(state => state?.phase === 'done').length }
}
