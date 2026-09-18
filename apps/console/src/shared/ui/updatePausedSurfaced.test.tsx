import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

let onMessage: ((m: { type: string; data?: Record<string, unknown> }) => void) | null = null

vi.mock('../data/useChatSocket', () => ({
  useChatSocket: (handler: (m: { type: string; data?: Record<string, unknown> }) => void) => {
    onMessage = handler
  },
}))

const cancelUpdate = vi.fn()
vi.mock('../data/api', () => ({
  api: {
    status: () => Promise.resolve({ update_progress: null }),
    cancelUpdate: (...a: unknown[]) => { cancelUpdate(...a); return Promise.resolve({ ok: true }) },
  },
}))

import { UpdateProgressOverlay } from './UpdateProgressOverlay'

const push = (step: string, detail: string) => onMessage?.({ type: 'update_progress', data: { step, detail } })

beforeEach(() => {
  onMessage = null
  cancelUpdate.mockClear()
  document.body.innerHTML = ''
})

describe('the updater exposes a paused state, not silence', () => {
  it('a paused step names the state and the way out', async () => {
    render(<UpdateProgressOverlay />)
    await waitFor(() => expect(onMessage).not.toBeNull())
    push('paused', 'Working tree has uncommitted changes to tracked files — commit or stash them and the update applies on the next check.')

    expect(await screen.findByText('Update paused')).toBeTruthy()
    expect(screen.getByText(/commit or stash them/)).toBeTruthy()
  })

  it('a paused update is not shown as a running pipeline', async () => {
    render(<UpdateProgressOverlay />)
    await waitFor(() => expect(onMessage).not.toBeNull())
    push('paused', 'commit or stash first')

    await screen.findByText('Update paused')
    expect(screen.queryByText('Installing dependencies')).toBeNull()
    expect(screen.queryByText('Building frontend')).toBeNull()
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeTruthy()
  })

  it('a staged update says it is waiting rather than showing a stalled pipeline', async () => {
    render(<UpdateProgressOverlay />)
    await waitFor(() => expect(onMessage).not.toBeNull())
    push('staged', 'Update staged — waiting for active work to finish…')

    expect(await screen.findByText(/waiting for active work/)).toBeTruthy()
    expect(screen.queryByText('Pulling')).toBeNull()
  })

  it('a real pipeline step still shows the stepper — the guard is not vacuous', async () => {
    render(<UpdateProgressOverlay />)
    await waitFor(() => expect(onMessage).not.toBeNull())
    push('installing', 'Installing package…')

    expect(await screen.findByText('Installing dependencies')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy()
  })

  it('dismissing a paused overlay clears it', async () => {
    render(<UpdateProgressOverlay />)
    await waitFor(() => expect(onMessage).not.toBeNull())
    push('paused', 'commit or stash first')

    fireEvent.click(await screen.findByRole('button', { name: 'Dismiss' }))
    await waitFor(() => expect(cancelUpdate).toHaveBeenCalled())
  })
})
