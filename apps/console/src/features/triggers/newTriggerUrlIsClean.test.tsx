import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'


const { EMPTY } = vi.hoisted(() => ({ EMPTY: [] as unknown[] }))

vi.mock('../../shared/data/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    schedules: () => Promise.resolve({ jobs: EMPTY }),
    hooks: () => Promise.resolve(EMPTY),
    storeTriggers: () => Promise.resolve(EMPTY),
    eventTriggers: () => Promise.resolve(EMPTY),
    actionProviders: () => Promise.resolve(EMPTY),
    autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
  },
}))

const { TriggersSection } = await import('./TriggersSection')

const mount = () => {
  const navigate = vi.fn()
  const view = render(
    <TriggersSection sub="" navigate={navigate} navEpoch={0} query={{}} setQuery={() => {}} />,
  )
  return { ...view, navigate }
}

describe('the header create button', () => {
  beforeEach(() => vi.clearAllMocks())

  it('navigates to the blank create flow with NO preset in the URL', async () => {
    const { navigate } = mount()
    const button = await waitFor(() => screen.getByRole('button', { name: /new trigger/i }))
    await userEvent.click(button)

    expect(navigate).toHaveBeenCalledTimes(1)
    const target = String(navigate.mock.calls[0][0])
    expect(target, 'the blank path must not carry a preset at all').not.toMatch(/preset=/)
    expect(target, 'and certainly not a stringified event object').not.toMatch(/object%20Object|object Object/)
    expect(target).toBe('triggers/new')
  })
})
