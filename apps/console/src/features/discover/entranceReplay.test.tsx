import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

import { DiscoverPage } from './DiscoverPage'


const discover = vi.fn()
const dismissDiscoverTip = vi.fn((_id: string) => Promise.resolve({}))
const clearDismissedDiscoverTips = vi.fn(() => Promise.resolve({}))

vi.mock('../../shared/data/api', () => ({
  api: {
    discover: () => discover(),
    dismissDiscoverTip: (id: string) => dismissDiscoverTip(id),
    clearDismissedDiscoverTips: () => clearDismissedDiscoverTips(),
  },
}))

function payload(tipIds: string[]) {
  return {
    enabled: true,
    visible_count: tipIds.length,
    areas: [
      {
        area: 'Chat',
        tips: tipIds.map((id) => ({
          id, title: `Tip ${id}`, lesson: 'What it teaches.',
          try_it: { label: 'Try it', route: 'chat', query: {} },
        })),
      },
      {
        area: 'Tasks',
        tips: [{ id: 'keep', title: 'Tip keep', lesson: 'Stays.', try_it: { label: 'Try it', route: 'tasks', query: {} } }],
      },
    ],
  }
}

const group = () => document.querySelector<HTMLElement>('[data-entrance]')
const regions = () => [...document.querySelectorAll<HTMLElement>('[data-entrance-region]')]

beforeEach(() => {
  discover.mockReset()
  dismissDiscoverTip.mockClear()
  clearDismissedDiscoverTips.mockClear()
  discover.mockResolvedValue(payload(['a', 'b']))
})

describe('Discover stages its regions', () => {
  it('renders one entrance group whose regions are the intro and each area band', async () => {
    render(<DiscoverPage navigate={() => {}} />)
    await screen.findByText('Chat')
    await waitFor(() => expect(regions()).toHaveLength(3))
    expect(group()).toHaveAttribute('data-entrance', 'staggered')
  })

  it('the tour card and every tip are on screen regardless of the cascade', async () => {
    render(<DiscoverPage navigate={() => {}} />)
    expect(screen.getByRole('button', { name: 'Start the tour' })).toBeInTheDocument()
    expect(await screen.findByText('Tip a')).toBeInTheDocument()
    expect(screen.getByText('Tip keep')).toBeInTheDocument()
  })
})

describe('a dismiss does not replay the entrance', () => {
  it('refetching after a dismiss keeps the same group and area nodes', async () => {
    render(<DiscoverPage navigate={() => {}} />)
    await screen.findByText('Tip a')
    await waitFor(() => expect(regions()).toHaveLength(3))
    const before = { g: group(), r: regions() }

    discover.mockResolvedValue(payload(['b']))
    screen.getAllByRole('button', { name: /^Dismiss/ })[0].click()

    await waitFor(() => expect(screen.queryByText('Tip a')).toBeNull())
    expect(dismissDiscoverTip).toHaveBeenCalledWith('a')
    expect(group()).toBe(before.g)
    expect(regions()).toEqual(before.r)
  })
})

describe('restoring Discover tips', () => {
  it('clears every dismissal only when one can become visible again', async () => {
    discover.mockResolvedValue({ enabled: true, visible_count: 0, areas: [], restorable_count: 1 })
    render(<DiscoverPage navigate={() => {}} />)

    screen.getByRole('button', { name: 'Restore dismissed tips' }).click()

    await waitFor(() => expect(clearDismissedDiscoverTips).toHaveBeenCalledOnce())
  })
})
