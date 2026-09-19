import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowLifecycleEvent } from './useWorkflowStream'
import type { WorkflowRunDetailData } from '../../shared/data/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'
import { foldEvent, foldSnapshot } from './workflowFold'

let lifecycle: ((event: WorkflowLifecycleEvent, data: unknown) => void) | undefined

const run = (): WorkflowRunDetailData => ({
  run_id: 'run-30',
  workflow: 'resume-work',
  status: 'running',
  spec_version: 1,
  nodes: [{ instance_path: 'root.children[0]', node_id: 'reuse', state: 'running' }],
})

vi.mock('./useWorkflowStream', async (importActual) => {
  const actual = await importActual<typeof import('./useWorkflowStream')>()
  return {
    ...actual,
    useWorkflowStream: (_runId: string, _enabled: boolean, handlers: { onLifecycle: typeof lifecycle }) => {
      lifecycle = handlers.onLifecycle
      return { connected: true }
    },
  }
})

vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRun: vi.fn(async () => run()),
      workflowContinuations: vi.fn(async () => ({ continuations: [] })),
    },
  }
})

vi.mock('./DeliverablePanel', () => ({ DeliverablePanel: () => null }))

beforeEach(() => { lifecycle = undefined })

describe('cached run nodes', () => {
  it('carries the cache hit through the lifecycle fold and progress snapshots', () => {
    const cached = foldEvent(foldSnapshot(run()), 'workflow_node_done', {
      run_id: 'run-30', instance_path: 'root.children[0]', node_id: 'reuse', status: 'done', cached: true,
    })
    const progressed = foldEvent(cached, 'workflow_progress', {
      run_id: 'run-30', nodes: [{ instance_path: 'root.children[0]', node_id: 'reuse', state: 'done' }],
    })
    expect(progressed.nodes[0].cached).toBe(true)
  })

  it('labels a cache-served node in the run DAG', async () => {
    render(<WorkflowRunDetail runId="run-30" onBack={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Graph' }))

    act(() => lifecycle?.('workflow_node_done', {
      run_id: 'run-30', instance_path: 'root.children[0]', node_id: 'reuse', status: 'done', cached: true,
    }))

    expect(screen.getByText('reuse · cached')).toBeInTheDocument()
  })
})
