import { useEffect, useRef, useState } from 'react'
import { api, type Loop } from '../../shared/data/api'

export const RUN_LIFECYCLE = [
  'new_finding', 'cycle_verdict', 'cycle_score', 'judge_error', 'complete', 'stagnant',
  'needs_input', 'failed', 'ratchet_regression', 'plan_step', 'phase_advance', 'rolled_back',
  'queued', 'autopilot', 'deleted', 'judge_blind', 'ship_blocked',
  'stage_advance', 'stage_stalled', 'gate_check', 'task_started', 'task_done', 'blocked',
  'breaker_trip', 'steering', 'judge_verdict', 'judge_divergence',
  'plan_streaming', 'revision', 'confirmation', 'demotion',
  'workflow_run_update', 'workflow_node_started', 'workflow_node_done', 'workflow_attention',
  'workflow_needs_input', 'workflow_gate_resolved', 'workflow_gate_revised',
  'workflow_spec_updated', 'workflow_mutation_rejected', 'workflow_forked', 'workflow_progress',
  'workflow_task_materialized', 'workflow_confirmation_pending', 'workflow_confirmation_resolved',
  'workflow_task_verified', 'workflow_cascade_blocked', 'workflow_steering_consumed',
  'workflow_loop_converged',
] as const

export type RunLifecycleEvent = (typeof RUN_LIFECYCLE)[number]

export const RUN_BATCH_EVENT = 'workflow_batch'

interface BatchMember { event: string; payload: unknown }

export function unwrapRunBatch(data: unknown): Array<{ event: RunLifecycleEvent; data: unknown }> {
  const raw = (data as { events?: unknown })?.events
  if (!Array.isArray(raw)) return []
  const known = new Set<string>(RUN_LIFECYCLE)
  return raw
    .filter((m): m is BatchMember => !!m && typeof (m as BatchMember).event === 'string')
    .filter((m) => known.has(m.event))
    .map((m) => ({ event: m.event as RunLifecycleEvent, data: m.payload }))
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
      try { ref.current.onSnapshot(JSON.parse((e as MessageEvent).data) as Loop) } catch {   }
    })
    for (const ev of RUN_LIFECYCLE) {
      es.addEventListener(ev, (e) => {
        let data: unknown = null
        try { data = JSON.parse((e as MessageEvent).data) } catch {   }
        ref.current.onLifecycle(ev, data)
      })
    }
    es.addEventListener(RUN_BATCH_EVENT, (e) => {
      let data: unknown = null
      try { data = JSON.parse((e as MessageEvent).data) } catch { return }
      for (const m of unwrapRunBatch(data)) ref.current.onLifecycle(m.event, m.data)
    })
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    return () => { es?.close(); setConnected(false) }
  }, [id, enabled])

  return { connected }
}
