import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { fireEvent } from '@testing-library/react'
import { useState } from 'react'
import type { Artifact, WorkflowIntrospection, WorkflowTimelineRow } from '../../../data/api'
import {
  ArtifactComparison, ArtifactMath, ArtifactSpecSheet, WorkflowActivityGraph,
  WorkflowJobProgress, WorkflowTimeline,
} from '../../../../features/chat/auiStructuredResults'
import { JobProgress } from '../../../vendor/assistant-ui/elements/job-progress'

const artifact: Artifact = {
  slug: 'run-rows', name: 'Run rows', kind: 'json', source: 'chat', description: '', tags: [], version: 3,
  created_at: '2026-09-26', updated_at: '2026-09-26', content: '[{"stage":"fetch"}]',
  events: [], source_path: '', readonly: false,
}
const row: WorkflowTimelineRow = {
  kind: 'node', ts: '2026-09-26T10:00:00Z', node_id: 'fetch', instance_path: 'fetch', state: 'completed',
  model: 'model-a', detail: 'Fetched the source',
}
const stats = {
  run_id: 'run-1', tokens: null, tokens_recorded: false, cached_tokens: 0, cost_usd: 0, priced: false,
  steps_completed: 1, steps_failed: 0, steps_cached: 0, duration_secs: 12, first_byte_ms: 0,
  models: [], unverified_steps: 1, verification_debt: 1, cache_hit_rate: 0,
}
const edges = { branches: {}, judges: {} }
const proof = { summary: '', verified_steps: 0, total_steps: 1, coverage: 0, evidence_files: [], warnings: [], honest: false }
const workflow: WorkflowIntrospection = {
  run_id: 'run-1', workflow: 'Source scan', stats, gates: {}, edges,
  template_card: { template: 'scan', runs: 1, cost_p50: 0, cost_p95: 0, priced: false,
    duration_p50: 12, duration_p95: 12, failure_rate: 0, warnings: [] },
  proof, timeline: [row], touched: [],
  answers: {
    running: { status: 'completed', workflow: 'Source scan', nodes: [] }, changed: [row], blocked: [],
    approval: [], failed: [], cost: stats,
    risky: { degraded: [], gates: [], edges, verification_debt: 1 },
    next: { action: 'nothing', detail: '', queued: [] }, proof,
  }, checklist_gaps: [],
}

describe('workflow records', () => {
  it('counts only valid timestamped activity from the real timeline', () => {
    render(<WorkflowActivityGraph workflow={{ ...workflow, timeline: [row, { ...row, ts: 'invalid' }] }} />)
    expect(screen.getByText('1 timestamped events')).toBeTruthy()
    expect(document.querySelector('[data-slot="activity-graph"]')).toBeTruthy()
    expect(screen.getByText('Source scan')).toBeTruthy()
  })

  it('shows no bars when there are no event timestamps', () => {
    render(<WorkflowActivityGraph workflow={{ ...workflow, timeline: [] }} />)
    expect(screen.getByText('No timestamped activity.')).toBeTruthy()
  })

  it('prints actual workflow event time, node, state, and detail', () => {
    render(<WorkflowTimeline rows={[row]} />)
    expect(screen.getByText('2026-09-26T10:00:00Z')).toBeTruthy()
    expect(screen.getByText('fetch · completed')).toBeTruthy()
    expect(screen.getByText('Fetched the source')).toBeTruthy()
  })

  it('uses the recorded proof for progress without inventing an ETA or cancellation', () => {
    render(<WorkflowJobProgress workflow={workflow} />)
    expect(screen.getByText('1 completed · 0 failed · 1 unverified')).toBeTruthy()
    expect(screen.getByText('Run run-1')).toBeTruthy()
    expect(screen.getByRole('progressbar', { name: 'Source scan progress' }).getAttribute('aria-valuenow')).toBe('0')
    expect(screen.getByText('0/1 steps verified')).toBeTruthy()
    expect(screen.queryByText('unknown')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Cancel the job' })).toBeNull()
  })
})

describe('artifact structure', () => {
  it('renders a mathematical expression exactly as supplied by its artifact result', () => {
    render(<ArtifactMath result={{ artifact, expression: 'E = mc²', note: 'Recorded derivation' }} />)
    expect(screen.getByText('E = mc²')).toBeTruthy()
    expect(screen.getByText('Recorded derivation')).toBeTruthy()
    expect(screen.getByText('Run rows')).toBeTruthy()
  })

  it('does not render an empty expression as a result', () => {
    const { container } = render(<ArtifactMath result={{ artifact, expression: '  ' }} />)
    expect(container.querySelector('[data-slot="math"]')).toBeNull()
  })

  it('uses only scalar values in a recorded JSON object for a spec sheet', () => {
    render(<ArtifactSpecSheet artifact={{ ...artifact, content: '{"model":"x","version":0,"enabled":false,"nested":{"x":1}}' }} />)
    expect(screen.getByText('model')).toBeTruthy()
    expect(screen.getByText('x')).toBeTruthy()
    expect(screen.getByText('0')).toBeTruthy()
    expect(screen.getByText('false')).toBeTruthy()
    expect(screen.queryByText('nested')).toBeNull()
  })

  it('rejects malformed and non-object spec artifacts', () => {
    const { container, rerender } = render(<ArtifactSpecSheet artifact={{ ...artifact, content: '{' }} />)
    expect(container.querySelector('[data-slot="spec-sheet"]')).toBeNull()
    rerender(<ArtifactSpecSheet artifact={artifact} />)
    expect(container.querySelector('[data-slot="spec-sheet"]')).toBeNull()
    rerender(<ArtifactSpecSheet artifact={{ ...artifact, kind: 'text' }} />)
    expect(container.querySelector('[data-slot="spec-sheet"]')).toBeNull()
  })

  it('compares actual row counts and version identities', () => {
    render(<ArtifactComparison before={artifact} after={{ ...artifact, name: 'Updated rows', version: 4,
      content: '[{"stage":"check"},{"stage":"write"}]' }} />)
    expect(screen.getByText('Run rows')).toBeTruthy()
    expect(screen.getByText('Updated rows')).toBeTruthy()
    expect(screen.getByText('1 rows · version 3')).toBeTruthy()
    expect(screen.getByText('2 rows · version 4')).toBeTruthy()
    expect(screen.getByText('Row 1, stage: fetch')).toBeTruthy()
    expect(screen.getByText('Row 1, stage: check')).toBeTruthy()
    expect(screen.queryByText('pick')).toBeNull()
  })

  it('does not compare artifacts without tabular records', () => {
    const { container } = render(<ArtifactComparison before={artifact} after={{ ...artifact, kind: 'markdown' }} />)
    expect(container.querySelector('[data-slot="comparison"]')).toBeNull()
  })
})

describe('adopted donor job progress control', () => {
  it('omits cancel when no actual cancellation action was supplied', () => {
    render(<JobProgress title="Recorded run" stages={[{ name: 'Collect', weight: 1 }, { name: 'Write', weight: 1 }]}
      stageIndex={0} stageProgress={0.5} eta="unknown" />)
    expect(screen.getByRole('progressbar', { name: 'Recorded run progress' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Cancel the job' })).toBeNull()
  })

  it('connects a supplied cancel action to visible state', () => {
    function Harness() {
      const [cancelled, setCancelled] = useState(false)
      return <><JobProgress title="Recorded run" stages={[{ name: 'Collect', weight: 1 }]}
        stageIndex={0} stageProgress={0.5} eta="unknown" onCancel={() => setCancelled(true)} />
        <output>{cancelled ? 'cancel requested' : 'running'}</output></>
    }
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel the job' }))
    expect(screen.getByRole('status').textContent).toBe('cancel requested')
  })
})
