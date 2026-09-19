import { describe, it, expect } from 'vitest'
import { RUN_LIFECYCLE, normalizeRunSnapshot, runPhaseId } from './useRunStream'

describe('RUN_LIFECYCLE loop-stream contract', () => {
  it('contains exactly the events backed by a loop SSE publisher', () => {
    expect(new Set(RUN_LIFECYCLE)).toEqual(new Set([
      'new_finding', 'cycle_verdict', 'judge_error', 'complete', 'stagnant',
      'needs_input', 'failed', 'ratchet_regression', 'plan_step', 'phase_advance', 'rolled_back',
      'queued', 'autopilot', 'deleted', 'judge_blind', 'ship_blocked',
      'stage_advance', 'stage_stalled', 'gate_check', 'task_started', 'task_done', 'blocked',
    ]))
  })

  it('has no duplicate members (a dup double-registers a listener)', () => {
    expect(new Set(RUN_LIFECYCLE).size).toBe(RUN_LIFECYCLE.length)
  })
})

describe('run snapshot phase IDs', () => {
  it('uses stage, then step, then title as the one phase-ID vocabulary', () => {
    expect(runPhaseId({ stage: 'verification', step: 'ignored', title: 'Ignored' })).toBe('verification')
    expect(runPhaseId({ stage: ' ', step: 'foundations', title: 'Foundations' })).toBe('foundations')
    expect(runPhaseId({ step: '', title: 'Fallback title' })).toBe('Fallback title')
  })

  it('projects design step IDs into the stage vocabulary used by run folds', () => {
    const loop = normalizeRunSnapshot({
      id: 'd1', kind: 'design', name: 'Design', task: 'Build it', execution: 'solo',
      agent: 'gideon-loop', model: '', attended: false, max_cycles: 30, idle_secs: 60,
      success_criteria: null, status: 'running', total_cycles: 1, error_message: null,
      created_at: 1, started_at: 1, completed_at: null, kind_config: {},
      plan: [{ step: 'foundations', title: 'Foundations & audit' }],
      phase_status: { foundations: 'active' },
    })
    expect(loop.plan?.[0].stage).toBe('foundations')
    expect(loop.phase_status?.[String(loop.plan?.[0].stage)]).toBe('active')
  })
})
