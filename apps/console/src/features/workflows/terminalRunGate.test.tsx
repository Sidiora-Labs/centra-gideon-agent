import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { WorkflowContinuation, WorkflowRunDetailData, WorkflowRunStatus } from '../../shared/data/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'

let status: WorkflowRunStatus = 'complete'

const continuation: WorkflowContinuation = {
  resume_token: 'stale-gate',
  node_id: 'approval',
  instance_path: 'approval',
  ask: { kind: 'approval', prompt: 'Approve this run?' },
  handoff: {},
  expires_at: Date.now() + 60_000,
  expired: false,
}

vi.mock('./useWorkflowStream', () => ({
  useWorkflowStream: () => ({ connected: false }),
}))

vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRun: vi.fn(async (): Promise<WorkflowRunDetailData> => ({
        run_id: 'terminal-run',
        workflow: 'approval-workflow',
        status,
        spec_version: 1,
        nodes: [{ instance_path: 'approval', node_id: 'approval', state: 'awaiting' }],
      })),
      workflowContinuations: vi.fn(async () => ({ continuations: [continuation] })),
    },
  }
})

vi.mock('./DeliverablePanel', () => ({ DeliverablePanel: () => null }))

describe.each<WorkflowRunStatus>(['complete', 'failed', 'cancelled'])('a %s workflow run', (terminalStatus) => {
  it('does not advertise a stale approve or deny gate', async () => {
    status = terminalStatus
    render(<WorkflowRunDetail runId="terminal-run" onBack={() => {}} />)

    fireEvent.click(await screen.findByRole('tab', { name: 'Graph' }))

    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Deny' })).not.toBeInTheDocument()
    expect(screen.queryByText('Approve this run?')).not.toBeInTheDocument()
  })
})
