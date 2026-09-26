import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { WorkflowIntrospection, WorkflowTimelineRow } from '../../../data/api'
import { WorkflowActivityGraph } from '../../../../features/chat/auiStructuredResults'

const event: WorkflowTimelineRow = {
  kind: 'node', ts: '2026-09-26T10:00:00Z', node_id: 'collect', instance_path: 'collect',
  state: 'completed', model: 'provider/model', detail: 'A recorded event',
}
const stats = {
  run_id: 'run-heat', tokens: null, tokens_recorded: false, cached_tokens: 0, cost_usd: 0,
  priced: false, steps_completed: 2, steps_failed: 0, steps_cached: 0, duration_secs: 5,
  first_byte_ms: 0, models: [], unverified_steps: 0, verification_debt: 0, cache_hit_rate: 0,
}
const edges = { branches: {}, judges: {} }
const proof = { summary: '', verified_steps: 0, total_steps: 2, coverage: 0, evidence_files: [], warnings: [], honest: false }
const workflow: WorkflowIntrospection = {
  run_id: 'run-heat', workflow: 'Heat run', stats, gates: {}, edges,
  template_card: { template: 'heat', runs: 1, cost_p50: 0, cost_p95: 0, priced: false,
    duration_p50: 5, duration_p95: 5, failure_rate: 0, warnings: [] },
  proof, timeline: [event, { ...event, ts: '2026-09-27T11:00:00Z', node_id: 'write' }], touched: [],
  answers: {
    running: { status: 'complete', workflow: 'Heat run', nodes: [] }, changed: [], blocked: [], approval: [],
    failed: [], cost: stats, risky: { degraded: [], gates: [], edges, verification_debt: 0 },
    next: { action: 'nothing', detail: '', queued: [] }, proof,
  }, checklist_gaps: [],
}

describe('heat graph from recorded workflow timestamps', () => {
  it('uses the donor activity primitive with exact dated events', () => {
    const { container } = render(<WorkflowActivityGraph workflow={workflow} />)
    expect(container.querySelector('[data-slot="activity-graph"]')).toBeTruthy()
    expect(screen.getByText('Heat run')).toBeTruthy()
    expect(screen.getByText('2 timestamped events')).toBeTruthy()
  })

  it('does not produce heat from events without valid timestamps', () => {
    const { container } = render(<WorkflowActivityGraph workflow={{ ...workflow, timeline: [{ ...event, ts: 'not-a-date' }] }} />)
    expect(screen.getByText('No timestamped activity.')).toBeTruthy()
    expect(container.querySelector('[data-slot="activity-graph"]')).toBeTruthy()
  })
})
