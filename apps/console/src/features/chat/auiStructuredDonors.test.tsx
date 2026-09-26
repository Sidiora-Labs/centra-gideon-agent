import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { Artifact, TaskGraphData, WorkflowIntrospection, WorkflowTimelineRow } from '../../shared/data/api'
import { ArtifactComparison, TaskFlowGraph, WorkflowJobProgress, WorkflowTimeline } from './auiStructuredResults'

const graph: TaskGraphData = {
  tasks: [
    { id: 'collect', title: 'Collect the complete evidence set', status: 'done' },
    { id: 'review', title: 'Review evidence', status: 'running' },
    { id: 'publish', title: 'Publish report', status: 'blocked' },
  ],
  edges: [
    { from: 'collect', to: 'review', type: 'BLOCKS' },
    { from: 'review', to: 'publish', type: 'BLOCKS' },
  ],
  analysis: { completion_pct: 33, root_task_ids: ['collect'], leaf_task_ids: ['publish'],
    critical_path: ['collect', 'review', 'publish'], cycles: [] },
}

const before: Artifact = {
  slug: 'run-rows', name: 'Run rows', kind: 'json', source: 'chat', description: '', tags: [],
  version: 3, created_at: '2026-09-26', updated_at: '2026-09-26', events: [], source_path: '', readonly: false,
  content: '[{"stage":"fetch","attempts":0,"approved":false},{"stage":"review"}]',
}
const after: Artifact = {
  ...before, version: 4, name: 'Updated rows',
  content: '[{"stage":"check","attempts":0,"approved":true},{"stage":"review"},{"stage":"publish"}]',
}

const row: WorkflowTimelineRow = {
  kind: 'node', ts: '2026-09-26T10:00:00Z', node_id: 'fetch', instance_path: 'fetch', state: 'completed',
  model: 'model-a', detail: 'Fetched the source',
}
const stats = {
  run_id: 'run-1', tokens: null, tokens_recorded: false, cached_tokens: 0, cost_usd: 0, priced: false,
  steps_completed: 3, steps_failed: 1, steps_cached: 0, duration_secs: 12, first_byte_ms: 0,
  models: [], unverified_steps: 2, verification_debt: 2, cache_hit_rate: 0,
}
const edges = { branches: {}, judges: {} }
const proof = { summary: 'Two verified steps', verified_steps: 2, total_steps: 4, coverage: 0.5,
  evidence_files: [], warnings: [], honest: true }
const workflow: WorkflowIntrospection = {
  run_id: 'run-1', workflow: 'Source scan', stats, gates: {}, edges,
  template_card: { template: 'scan', runs: 1, cost_p50: 0, cost_p95: 0, priced: false,
    duration_p50: 12, duration_p95: 12, failure_rate: 0, warnings: [] },
  proof, timeline: [row], touched: [],
  answers: {
    running: { status: 'running', workflow: 'Source scan', nodes: [] }, changed: [row], blocked: [],
    approval: [], failed: [], cost: stats,
    risky: { degraded: [], gates: [], edges, verification_debt: 2 },
    next: { action: 'waits', detail: '', queued: [] }, proof,
  }, checklist_gaps: [],
}

describe('recorded task graph in the donor flow graph', () => {
  it('shows complete task labels, actual status, and only recorded edges without inventing an action', () => {
    const { container } = render(<TaskFlowGraph graph={{ ...graph, edges: [...graph.edges,
      { from: 'missing', to: 'review', type: 'BLOCKS' }] }} />)
    const donor = within(container.querySelector('[data-slot="flow-graph"]') as HTMLElement)
    expect(donor.getByText('Collect the complete evidence set')).toBeInTheDocument()
    expect(donor.getByText('running')).toBeInTheDocument()
    expect(donor.getByText('blocked')).toBeInTheDocument()
    expect(donor.queryByRole('button')).toBeNull()
    expect(container.querySelectorAll('svg path')).toHaveLength(2)
    expect(container.querySelectorAll('svg rect, svg text')).toHaveLength(0)
  })

  it('opens the exact persisted task ID from a donor node only when navigation is supplied', () => {
    const onOpen = vi.fn()
    render(<TaskFlowGraph graph={graph} onOpen={onOpen} />)
    fireEvent.click(screen.getByRole('button', { name: 'Review evidence, running' }))
    expect(onOpen).toHaveBeenCalledExactlyOnceWith('review')
    expect(screen.getAllByRole('button')).toHaveLength(graph.tasks.length)
  })

  it('keeps the task title as the accessible action when the producer gives no status', () => {
    const onOpen = vi.fn()
    render(<TaskFlowGraph graph={{ ...graph, tasks: [{ ...graph.tasks[0], status: '' }], edges: [] }} onOpen={onOpen} />)
    fireEvent.click(screen.getByRole('button', { name: 'Collect the complete evidence set' }))
    expect(onOpen).toHaveBeenCalledExactlyOnceWith('collect')
    expect(screen.queryByText('done')).toBeNull()
  })

  it('does not fabricate nodes or edges for an empty task graph', () => {
    const { container } = render(<TaskFlowGraph graph={{ ...graph, tasks: [], edges: graph.edges }} />)
    expect(container.querySelector('[data-slot="flow-graph"]')).toBeInTheDocument()
    expect(container.querySelector('svg path')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('recorded artifact rows in the donor comparison card', () => {
  it('compares changed, added, zero, and boolean cells without asserting a recommendation', () => {
    const { container } = render(<ArtifactComparison before={before} after={after} />)
    const donor = within(container.querySelector('[data-slot="comparison-card"]') as HTMLElement)
    expect(donor.getByText('Run rows')).toBeInTheDocument()
    expect(donor.getByText('Updated rows')).toBeInTheDocument()
    expect(donor.getByText('2 rows · version 3')).toBeInTheDocument()
    expect(donor.getByText('3 rows · version 4')).toBeInTheDocument()
    expect(donor.getByText('Row 1, stage: fetch')).toBeInTheDocument()
    expect(donor.getByText('Row 1, stage: check')).toBeInTheDocument()
    expect(donor.getByText('Row 1, approved: false')).toBeInTheDocument()
    expect(donor.getByText('Row 1, approved: true')).toBeInTheDocument()
    expect(donor.getByText('Row 3, stage')).toBeInTheDocument()
    expect(donor.getByText('Row 3, stage: publish')).toBeInTheDocument()
    expect(donor.queryByText('Row 1, attempts')).toBeNull()
    expect(donor.queryByText('pick')).toBeNull()
    expect(container.querySelector('[data-slot="comparison"]')).toBeInTheDocument()
  })

  it('keeps equal values out of the delta and refuses non-tabular content', () => {
    const { container, rerender } = render(<ArtifactComparison before={before} after={{ ...before, version: 4 }} />)
    expect(container.querySelector('[data-slot="comparison-card"]')).toBeInTheDocument()
    expect(screen.queryByText(/Row 1/)).toBeNull()
    rerender(<ArtifactComparison before={before} after={{ ...after, content: '{bad json' }} />)
    expect(container.querySelector('[data-slot="comparison-card"]')).toBeNull()
    rerender(<ArtifactComparison before={before} after={{ ...after, kind: 'markdown' }} />)
    expect(container.querySelector('[data-slot="comparison"]')).toBeNull()
  })
})

describe('recorded workflow events and proof in donor views', () => {
  it('shows timestamp, node, state, detail, and running state from supplied timeline rows', () => {
    const { container } = render(<WorkflowTimeline rows={[row, { ...row, ts: '2026-09-26T10:03:00Z',
      node_id: 'review', state: 'running', detail: 'Checking evidence' }]} />)
    const donor = within(container.querySelector('[data-slot="timeline"]') as HTMLElement)
    const timestamp = container.querySelector('time[datetime="2026-09-26T10:00:00Z"]')
    expect(timestamp).toBeInTheDocument()
    expect(timestamp?.textContent).not.toContain('T')
    expect(donor.getByText('fetch · completed')).toBeInTheDocument()
    expect(donor.getByText(/Fetched the source/)).toBeInTheDocument()
    expect(donor.getByText('review · running')).not.toHaveClass('font-medium')
    expect(donor.getByText(/Checking evidence/)).toBeInTheDocument()
    expect(container.querySelectorAll('[data-slot="timeline"] .w-px')).toHaveLength(1)
    expect(donor.queryByText(/future|scheduled/i)).toBeNull()
  })

  it('does not invent workflow events when the source timeline is empty', () => {
    const { container } = render(<WorkflowTimeline rows={[]} />)
    expect(container.querySelector('[data-slot="timeline"]')).toBeInTheDocument()
    expect(container.querySelector('[data-slot="timeline"]')?.textContent).toBe('')
  })

  it('announces only measured verification progress and preserves run statistics', () => {
    render(<WorkflowJobProgress workflow={workflow} />)
    const bar = screen.getByRole('progressbar', { name: 'Source scan progress' })
    expect(bar).toHaveAttribute('aria-valuenow', '50')
    expect((bar.firstElementChild as HTMLElement).style.width).toBe('50%')
    expect(screen.getByText('2/4 steps verified')).toBeInTheDocument()
    expect(screen.getByText('3 completed · 1 failed · 2 unverified')).toBeInTheDocument()
    expect(screen.getByText('Run run-1')).toBeInTheDocument()
    expect(screen.queryByText('unknown')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Cancel the job' })).toBeNull()
  })

  it('shows done only when the recorded proof is complete and hides absent totals', () => {
    const { rerender } = render(<WorkflowJobProgress workflow={{ ...workflow,
      proof: { ...proof, verified_steps: 4 } }} />)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100')
    expect(screen.getByText('done')).toBeInTheDocument()
    rerender(<WorkflowJobProgress workflow={{ ...workflow, proof: { ...proof, verified_steps: 0, total_steps: 0 } }} />)
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.queryByText('done')).toBeNull()
    expect(screen.getByText('0/0 steps verified')).toBeInTheDocument()
  })
})
