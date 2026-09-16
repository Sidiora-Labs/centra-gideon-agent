import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, within, fireEvent } from '@testing-library/react'

const deleteMock = vi.fn()

vi.mock('../../shared/data/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../shared/data/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      notifications: vi.fn(async () => ({ notifications: [ITEM] })),
      deleteNotification: (...a: unknown[]) => deleteMock(...a),
    },
  }
})
vi.mock('../../shared/data/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../shared/data/rungs', () => ({ useAutonomyLadder: () => ({ ladder: null, refresh: () => {} }) }))

import { NotificationsPage } from './NotificationsPage'
import { DialogHost } from '../../shared/ui/dialog/DialogHost'
import { subscribeDialogs, closeDialog } from '../../shared/ui/dialog/dialogStore'

const ITEM = {
  ts: 1785890207, kind: 'error', title: 'Inventory agent hit an error',
  body: 'boom', acked: true, mode: '', targets: [],
}

function mount() {
  return render(
    <>
      <NotificationsPage query={{}} setQuery={() => {}} navigate={() => {}} />
      <DialogHost />
    </>,
  )
}

async function openRowDeleteDialog() {
  const btn = await screen.findByRole('button', { name: /^Delete: / })
  fireEvent.click(btn)
  return await screen.findByRole('alertdialog')
}

beforeEach(() => { deleteMock.mockReset(); deleteMock.mockResolvedValue({}) })
afterEach(() => {
  let pending: unknown[] = []
  const unsub = subscribeDialogs((d) => { pending = d as unknown[] })
  unsub()
  for (const d of pending) closeDialog((d as { id: number }).id, false)
  cleanup()
})

describe('per-row notification delete confirms (#628)', () => {
  it('opens a danger dialog naming the notification, and deletes nothing yet', async () => {
    mount()
    const row = await screen.findByText('Inventory agent hit an error')
    fireEvent.click(row)
    const dialog = await openRowDeleteDialog()
    expect(within(dialog).getByText(/Delete notification "Inventory agent hit an error"\?/)).toBeTruthy()
    expect(within(dialog).getByText(/cannot be undone/i)).toBeTruthy()
    expect(deleteMock).not.toHaveBeenCalled()
  })

  it('dismissing the dialog deletes nothing', async () => {
    mount()
    fireEvent.click(await screen.findByText('Inventory agent hit an error'))
    const dialog = await openRowDeleteDialog()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(deleteMock).not.toHaveBeenCalled()
  })

  it('confirming deletes exactly once, with the row\u2019s ts', async () => {
    mount()
    fireEvent.click(await screen.findByText('Inventory agent hit an error'))
    const dialog = await openRowDeleteDialog()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(deleteMock).toHaveBeenCalledTimes(1))
    expect(deleteMock).toHaveBeenCalledWith(ITEM.ts)
  })
})
