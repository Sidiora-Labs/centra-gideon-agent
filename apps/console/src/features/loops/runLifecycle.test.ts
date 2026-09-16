import { describe, it, expect } from 'vitest'
import { RUN_LIFECYCLE, unwrapRunBatch } from './useRunStream'

describe('RUN_LIFECYCLE union membership', () => {
  it('carries the UNIVERSAL-PLANNING plan-review events (WF2UNI-10)', () => {
    for (const ev of ['plan_streaming', 'revision', 'confirmation', 'demotion'] as const) {
      expect(RUN_LIFECYCLE).toContain(ev)
    }
  })

  it('has no duplicate members (a dup double-registers a listener)', () => {
    expect(new Set(RUN_LIFECYCLE).size).toBe(RUN_LIFECYCLE.length)
  })
})

describe('the mirrored workflow-run events are registered', () => {
  it('carries every event the workflow engine publishes', () => {
    for (const ev of [
      'workflow_run_update', 'workflow_node_started', 'workflow_node_done', 'workflow_attention',
      'workflow_needs_input', 'workflow_gate_resolved', 'workflow_gate_revised',
      'workflow_spec_updated', 'workflow_mutation_rejected', 'workflow_forked',
      'workflow_progress', 'workflow_task_materialized', 'workflow_confirmation_pending',
      'workflow_confirmation_resolved', 'workflow_task_verified', 'workflow_cascade_blocked',
      'workflow_steering_consumed',
      'workflow_loop_converged',
    ] as const) {
      expect(RUN_LIFECYCLE).toContain(ev)
    }
  })

  it('stays in step with the workflow hook it mirrors', async () => {
    const { WORKFLOW_LIFECYCLE } = await import('../workflows/useWorkflowStream')
    const missing = WORKFLOW_LIFECYCLE.filter((e) => !RUN_LIFECYCLE.includes(e as never))
    expect(missing).toEqual([])
  })
})

describe('the coalesced batch frame is unwrapped on this hook too', () => {
  it('replays members in order, so a fold is identical batched or not', () => {
    const out = unwrapRunBatch({
      events: [
        { event: 'workflow_node_started', payload: { n: 1 } },
        { event: 'workflow_node_done', payload: { n: 2 } },
      ],
    })
    expect(out.map((m) => m.event)).toEqual(['workflow_node_started', 'workflow_node_done'])
    expect(out[1].data).toEqual({ n: 2 })
  })

  it('drops an unrecognized member rather than casting it', () => {
    const out = unwrapRunBatch({ events: [{ event: 'not_a_real_event', payload: {} }] })
    expect(out).toEqual([])
  })

  it('survives a malformed frame instead of throwing into the listener', () => {
    expect(unwrapRunBatch(null)).toEqual([])
    expect(unwrapRunBatch({ events: 'nope' })).toEqual([])
  })
})
