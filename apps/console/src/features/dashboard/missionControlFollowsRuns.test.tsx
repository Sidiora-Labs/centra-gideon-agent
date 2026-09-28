import { expect, it } from 'vitest'
import { toLanes, type WorkflowInput, type LoopInput, type AttentionInput } from '../../shared/data/attentionLanes'

const run: WorkflowInput = { id: 'run-1', workflow_name: 'Research', status: 'running', created_at: '2026-01-01T00:00:00Z' }
const loop: LoopInput = { run_id: 'run-1', name: 'Research loop', task: 'Research', status: 'running', started_at: 1, created_at: 1 }

it('shows one loop-backed parent with its live children nested', () => {
  const lanes = toLanes([], [], [], [loop], [run, { ...run, id: 'child-1', parent_run_id: run.id }])
  expect(lanes.working.map(card => card.id)).toEqual(['run-1'])
  expect(lanes.working[0].title).toBe('Research loop')
  expect(lanes.working[0].refs).toEqual({ workflow: 'run-1', children: ['child-1'] })
})

it('moves the same run on actual status changes and removes terminal work', () => {
  expect(toLanes([], [], [], [], [run]).working.map(card => card.id)).toEqual(['run-1'])
  expect(toLanes([], [], [], [], [{ ...run, status: 'paused' }]).idle.map(card => card.id)).toEqual(['run-1'])
  expect(toLanes([], [], [], [], [{ ...run, status: 'needs_input' }])['your-turn'].map(card => card.id)).toEqual(['run-1'])
  for (const status of ['complete', 'failed', 'cancelled', 'declined'] as const) {
    expect(Object.values(toLanes([], [], [], [], [{ ...run, status }])).flat()).toEqual([])
  }
})

it('keeps a live input request once instead of duplicating its workflow card', () => {
  const request: AttentionInput = { id: 'request-1', item_kind: 'needs_input', status: 'pending', message: 'Choose a source', sender_name: '', channel_name: 'System', refs: { workflow: run.id } }
  const lanes = toLanes([request], [], [], [], [{ ...run, status: 'needs_input' }])
  expect(lanes['your-turn'].map(card => card.key)).toEqual(['inbox:request-1'])
  expect(lanes.working).toEqual([])
})
