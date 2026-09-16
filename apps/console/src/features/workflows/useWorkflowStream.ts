import { useEffect, useRef, useState } from 'react'
import { api, type WorkflowRunDetailData } from '../../shared/data/api'

export const WORKFLOW_LIFECYCLE = [
  'workflow_run_update',
  'workflow_node_started',
  'workflow_node_done',
  'workflow_attention',
  'workflow_needs_input',
  'workflow_gate_resolved',
  'workflow_gate_revised',
  'workflow_spec_updated',
  'workflow_mutation_rejected',
  'workflow_forked',
  'workflow_progress',
  'workflow_task_materialized',
  'workflow_confirmation_pending',
  'workflow_confirmation_resolved',
  'workflow_task_verified',
  'workflow_cascade_blocked',
  'workflow_steering_consumed',
  'workflow_loop_converged',
] as const

export type WorkflowLifecycleEvent = (typeof WORKFLOW_LIFECYCLE)[number]

export const WORKFLOW_BATCH_EVENT = 'workflow_batch'

interface BatchMember { event: string; payload: unknown }

export function unwrapBatch(data: unknown): Array<{ event: WorkflowLifecycleEvent; data: unknown }> {
  const raw = (data as { events?: unknown })?.events
  if (!Array.isArray(raw)) return []
  const known = new Set<string>(WORKFLOW_LIFECYCLE)
  return raw
    .filter((m): m is BatchMember => !!m && typeof (m as BatchMember).event === 'string')
    .filter((m) => known.has(m.event))
    .map((m) => ({ event: m.event as WorkflowLifecycleEvent, data: m.payload }))
}

export function useWorkflowStream(
  runId: string | null,
  enabled: boolean,
  handlers: {
    onSnapshot: (run: WorkflowRunDetailData) => void
    onLifecycle: (event: WorkflowLifecycleEvent, data: unknown) => void
  },
) {
  const ref = useRef(handlers)
  ref.current = handlers
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    setConnected(false)
    if (!enabled || !runId) return
    let es: EventSource | null = null
    try { es = new EventSource(api.workflowRunStreamUrl(runId)) } catch { return }

    es.addEventListener('workflow_snapshot', (e) => {
      try { ref.current.onSnapshot(JSON.parse((e as MessageEvent).data) as WorkflowRunDetailData) } catch {   }
    })
    for (const ev of WORKFLOW_LIFECYCLE) {
      es.addEventListener(ev, (e) => {
        let data: unknown = null
        try { data = JSON.parse((e as MessageEvent).data) } catch {   }
        ref.current.onLifecycle(ev, data)
      })
    }
    es.addEventListener(WORKFLOW_BATCH_EVENT, (e) => {
      let data: unknown = null
      try { data = JSON.parse((e as MessageEvent).data) } catch { return }
      for (const m of unwrapBatch(data)) ref.current.onLifecycle(m.event, m.data)
    })
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    return () => { es?.close(); setConnected(false) }
  }, [runId, enabled])

  return { connected }
}
