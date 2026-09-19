import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const uLoopPlanState = vi.fn()
const uLoopPlanRetry = vi.fn()

vi.mock('../../shared/data/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoopPlanState: (...args: unknown[]) => uLoopPlanState(...args),
    uLoopPlanRetry: (...args: unknown[]) => uLoopPlanRetry(...args),
  },
}))

vi.mock('../../shared/ui/PlanningWalkthrough', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  PlanningWalkthrough: () => <div data-testid="planning-walkthrough" />,
}))

import { CodePlanningView } from './CodePlanningView'

const stalled = {
  session: null,
  planner: {
    active: false,
    stalled: true,
    retryable: true,
    started_at: 100,
    last_activity_at: 120,
    server_time: 500,
    stall_after_seconds: 180,
  },
}

const running = {
  ...stalled,
  planner: { ...stalled.planner, active: true, stalled: false, retryable: false, server_time: 501 },
}

describe('planner recovery', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    uLoopPlanState.mockResolvedValue(stalled)
    uLoopPlanRetry.mockResolvedValue({ ok: true, planning: true })
  })

  it('uses server liveness to expose Retry immediately after planner death', async () => {
    render(<CodePlanningView projectId="code-1" onReady={vi.fn()} onBack={vi.fn()} />)

    const retry = await screen.findByRole('button', { name: 'Retry planning' })
    expect(screen.queryByTestId('planning-walkthrough')).toBeNull()

    uLoopPlanState.mockResolvedValue(running)
    fireEvent.click(retry)

    await waitFor(() => expect(uLoopPlanRetry).toHaveBeenCalledWith('code-1'))
    await waitFor(() => expect(screen.getByTestId('planning-walkthrough')).toBeTruthy())
  })
})
