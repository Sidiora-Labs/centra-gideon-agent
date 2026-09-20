import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowContinuation, WorkflowRunDetailData } from '../../shared/data/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'

const confirmWorkflowRun = vi.fn()
const resumeWorkflowRun = vi.fn()

const run: WorkflowRunDetailData = {
  run_id: 'run-38', workflow: 'release', status: 'needs_input', spec_version: 1,
  nodes: [{ instance_path: 'root.children[0]', node_id: 'ship', state: 'awaiting' }],
}
const continuation: WorkflowContinuation = {
  resume_token: 'gate-token', node_id: 'ship', instance_path: 'root.children[0]',
  ask: { kind: 'approval', prompt: 'Ship this stage?' }, handoff: {}, expires_at: 0, expired: false,
}

vi.mock('./useWorkflowStream', () => ({ useWorkflowStream: () => ({ connected: true }) }))
vi.mock('./DeliverablePanel', () => ({ DeliverablePanel: () => null }))
vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRun: vi.fn(async () => run),
      workflowContinuations: vi.fn(async () => ({ continuations: [continuation] })),
      confirmWorkflowRun,
      resumeWorkflowRun,
    },
  }
})

describe('workflow stage approval in run detail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    confirmWorkflowRun.mockResolvedValue({ accepted: true })
  })

  it.each([
    ['Approve', 'approve'],
    ['Deny', 'reject'],
  ] as const)('answers %s through the existing gate route', async (label, verb) => {
    render(<WorkflowRunDetail runId="run-38" onBack={() => {}} />)

    fireEvent.click(await screen.findByRole('button', { name: label }))

    await waitFor(() => expect(confirmWorkflowRun).toHaveBeenCalledWith('run-38', {
      verb, resume_token: 'gate-token',
    }))
    expect(resumeWorkflowRun).not.toHaveBeenCalled()
  })
})
