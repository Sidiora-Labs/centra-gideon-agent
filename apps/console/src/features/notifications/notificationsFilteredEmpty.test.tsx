import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const ITEM = {
  ts: 1785890207, kind: 'info', title: 'Finished indexing', body: 'Done',
  acked: true, mode: '', targets: [],
}

vi.mock('../../shared/data/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../shared/data/api')>()
  return { ...mod, api: { ...mod.api, notifications: vi.fn(async () => ({ notifications: [ITEM] })) } }
})
vi.mock('../../shared/data/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../shared/data/rungs', () => ({ useAutonomyLadder: () => ({ ladder: null, refresh: () => {} }) }))

import { NotificationsPage } from './NotificationsPage'

afterEach(cleanup)

describe('filtered notification empty state', () => {
  it('offers to clear an active filter that matches no notifications', async () => {
    const setQuery = vi.fn()
    render(<NotificationsPage query={{ filter: 'unread' }} setQuery={setQuery} navigate={() => {}} />)

    expect(await screen.findByRole('heading', { name: 'No notifications match' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Clear filter' }))

    expect(setQuery).toHaveBeenCalledWith({ filter: null }, { replace: true })
  })
})
