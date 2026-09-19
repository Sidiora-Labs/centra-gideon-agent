import { useEffect, useRef, useState } from 'react'
import { api, type Loop } from '../../shared/data/api'

export const RUN_LIFECYCLE = [
  'new_finding', 'cycle_verdict', 'judge_error', 'complete', 'stagnant',
  'needs_input', 'failed', 'ratchet_regression', 'plan_step', 'phase_advance', 'rolled_back',
  'queued', 'autopilot', 'deleted', 'judge_blind', 'ship_blocked',
  'stage_advance', 'stage_stalled', 'gate_check', 'task_started', 'task_done', 'blocked',
] as const

export type RunLifecycleEvent = (typeof RUN_LIFECYCLE)[number]

const RUN_PHASE_ID_FIELDS = ['stage', 'step', 'title'] as const

export function runPhaseId(phase: NonNullable<Loop['plan']>[number]): string {
  for (const field of RUN_PHASE_ID_FIELDS) {
    const value = String(phase[field] ?? '').trim()
    if (value) return value
  }
  return ''
}

export function normalizeRunSnapshot(loop: Loop): Loop {
  if (!loop.plan?.length) return loop
  let changed = false
  const plan = loop.plan.map((phase) => {
    if (String(phase.stage ?? '').trim()) return phase
    const id = runPhaseId(phase)
    if (!id) return phase
    changed = true
    return { ...phase, stage: id }
  })
  return changed ? { ...loop, plan } : loop
}

export function useRunStream(id: string | null, enabled: boolean, handlers: {
  onSnapshot: (c: Loop) => void
  onLifecycle: (event: RunLifecycleEvent, data: unknown) => void
}) {
  const ref = useRef(handlers)
  ref.current = handlers

  const [connected, setConnected] = useState(false)

  useEffect(() => {
    setConnected(false)
    if (!enabled || !id) return
    let es: EventSource | null = null
    try { es = new EventSource(api.uLoopStreamUrl(id)) } catch { return }

    es.addEventListener('snapshot', (e) => {
      try { ref.current.onSnapshot(normalizeRunSnapshot(JSON.parse((e as MessageEvent).data) as Loop)) } catch {   }
    })
    for (const ev of RUN_LIFECYCLE) {
      es.addEventListener(ev, (e) => {
        let data: unknown = null
        try { data = JSON.parse((e as MessageEvent).data) } catch {   }
        ref.current.onLifecycle(ev, data)
      })
    }
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    return () => { es?.close(); setConnected(false) }
  }, [id, enabled])

  return { connected }
}
