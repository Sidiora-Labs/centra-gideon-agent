import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatPlanGate } from './ChatPlanGate'


const step = {
  id: 'chat-plan-1',
  kind: 'chat_plan',
  title: 'Plan',
  objective: 'Plan the work before anything runs.',
  status: 'awaiting_review',
  artifact: { markdown: '### Plan\n1. first' },
  comments: [],
}

const chatPlanSession = vi.fn()
const chatPlanEdit = vi.fn()

vi.mock('../../data/api', () => ({
  api: {
    chatPlanSession: (...a: unknown[]) => chatPlanSession(...a),
    chatPlanEdit: (...a: unknown[]) => chatPlanEdit(...a),
    chatPlanComment: vi.fn(),
    chatPlanApprove: vi.fn(),
    chatPlanCancel: vi.fn(),
  },
}))

beforeEach(() => {
  chatPlanSession.mockReset()
  chatPlanEdit.mockReset()
  chatPlanSession.mockResolvedValue({
    session: { project_id: 'c1', steps: [step] },
    awaiting_step_id: 'chat-plan-1',
    binding: {},
    task_mode: 'plan',
  })
  chatPlanEdit.mockResolvedValue({ ok: true, session: { project_id: 'c1', steps: [step] } })
})

describe('the plan gate’s editor', () => {
  it('returns to review after a successful save, so Approve is reachable', async () => {
    const user = userEvent.setup()
    render(<ChatPlanGate session="c1" refreshKey={0} onTaskMode={() => {}} />)

    await user.click(await screen.findByLabelText('Edit this plan'))
    const box = await screen.findByLabelText('Plan markdown')
    await user.clear(box)
    await user.type(box, 'edited by the operator')
    await user.click(screen.getByRole('button', { name: /Save edits/i }))

    await waitFor(() => expect(chatPlanEdit).toHaveBeenCalledTimes(1))
    expect(chatPlanEdit.mock.calls[0][2]).toBe('edited by the operator')

    await waitFor(() => expect(screen.queryByLabelText('Plan markdown')).toBeNull())
    expect(screen.getByRole('button', { name: /Approve & run it/i })).toBeTruthy()
  })

  it('keeps the editor open when the save fails, so the text is not lost', async () => {
    const user = userEvent.setup()
    chatPlanEdit.mockRejectedValue(new Error('storage is read-only'))
    render(<ChatPlanGate session="c1" refreshKey={0} onTaskMode={() => {}} />)

    await user.click(await screen.findByLabelText('Edit this plan'))
    const box = await screen.findByLabelText('Plan markdown')
    await user.clear(box)
    await user.type(box, 'work I do not want to retype')
    await user.click(screen.getByRole('button', { name: /Save edits/i }))

    await waitFor(() => expect(chatPlanEdit).toHaveBeenCalled())
    const still = await screen.findByLabelText('Plan markdown')
    expect((still as HTMLTextAreaElement).value).toBe('work I do not want to retype')
    expect(screen.getByText(/read-only/i)).toBeTruthy()
  })
})
