import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { NodeInspect, WorkflowRunDetailData } from '../../shared/data/api'
import { WorkflowProgressCard } from '../chat/WorkflowProgressCard'
import { WorkflowsSection } from './WorkflowsSection'

const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const workflowRunNodeInspect = vi.fn<(runId: string, nodeId: string) => Promise<NodeInspect>>()

vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: () => Promise.resolve({ continuations: [] }),
      workflowRunNodeInspect: (runId: string, nodeId: string) => workflowRunNodeInspect(runId, nodeId),
      workflowRunStreamUrl: () => 'http://localhost/stream',
    },
  }
})

const run = (nodeId = 'draft'): WorkflowRunDetailData => ({
  run_id: 'run/1', workflow: 'demo', status: 'running', spec_version: 1,
  nodes: [{ instance_path: 'root', node_id: nodeId, state: 'running' }],
})

const inspect = (nodeId: string): NodeInspect => ({
  run_id: 'run/1', node_id: nodeId, instance_path: 'root', state: 'done',
  resolved_prompt: 'Prompt', resolved_inputs: {}, output: 'Output', attempts: [], ledger_events: [], cached: false,
})

describe('workflow active-node deep links', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workflowRun.mockResolvedValue(run())
    workflowRunNodeInspect.mockImplementation(async (_runId, nodeId) => inspect(nodeId))
  })

  it('links the active chat row to the encoded run and node', async () => {
    render(<WorkflowProgressCard refObj={{ runId: 'run/1', created: false }} />)
    const link = await screen.findByTitle('Inspect the active node')
    expect(link).toHaveAttribute('href', '#/workflows/runs/run%2F1?node=draft')
    expect(link).toHaveTextContent('draft')
  })

  it('follows node query changes with one run-owned inspector and clears the query on close', async () => {
    const setQuery = vi.fn()
    const props = {
      sub: 'runs/run-1', navigate: vi.fn(), navEpoch: 0, setQuery,
    }
    const view = render(<WorkflowsSection {...props} query={{ node: 'draft' }} />)
    await waitFor(() => expect(workflowRunNodeInspect).toHaveBeenCalledWith('run-1', 'draft'))

    view.rerender(<WorkflowsSection {...props} query={{ node: 'review' }} />)
    await waitFor(() => expect(workflowRunNodeInspect).toHaveBeenCalledWith('run-1', 'review'))
    expect(screen.getAllByTestId('node-inspector-body')).toHaveLength(1)

    fireEvent.click(screen.getByLabelText('Close'))
    expect(setQuery).toHaveBeenCalledWith({ node: null }, { replace: true })
  })

  it('keeps the inspector out of the chat rail', () => {
    const modules = import.meta.glob('../chat/**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
    expect(Object.values(modules).filter((source) => source.includes('<NodeInspectorDrawer'))).toHaveLength(0)
  })
})
