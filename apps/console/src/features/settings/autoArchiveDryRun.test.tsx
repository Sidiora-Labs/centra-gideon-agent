import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type AutoArchiveSessionsResult } from '../../shared/data/api'
import { AutoArchiveRow } from './ChatPanel'

const result = (days: number, count: number): AutoArchiveSessionsResult => ({
  ok: true,
  enabled: days > 0,
  dry_run: true,
  days,
  keys: Array.from({ length: count }, (_, index) => `chat-${index}`),
  count,
})

describe('auto-archive settings preview', () => {
  afterEach(() => vi.restoreAllMocks())

  it('waits for the threshold save before previewing the newly labelled value', async () => {
    const preview = vi.spyOn(api, 'autoArchiveSessions')
      .mockResolvedValueOnce(result(30, 4))
      .mockResolvedValueOnce(result(7, 2))
    let finishSave: ((saved: boolean) => void) | undefined
    const onCommit = vi.fn(() => new Promise<boolean>((resolve) => { finishSave = resolve }))

    const view = render(<AutoArchiveRow days={30} onCommit={onCommit} saved={false} />)
    expect(await screen.findByText('4 stale now')).toBeTruthy()

    const input = screen.getByRole('spinbutton', { name: 'Auto-archive after (days)' })
    fireEvent.change(input, { target: { value: '7' } })
    fireEvent.blur(input)

    expect(onCommit).toHaveBeenCalledWith(7, 'Auto-archive after (days)')
    expect(screen.queryByText(/stale now/)).toBeNull()
    expect(preview).toHaveBeenCalledTimes(1)

    view.rerender(<AutoArchiveRow days={7} onCommit={onCommit} saved={false} />)
    await act(async () => { finishSave!(true) })
    expect(await screen.findByText('2 stale now')).toBeTruthy()
    expect(preview).toHaveBeenCalledTimes(2)
  })

  it('restores the saved threshold when the optimistic save fails', async () => {
    vi.spyOn(api, 'autoArchiveSessions').mockResolvedValue(result(30, 0))
    const onCommit = vi.fn().mockResolvedValue(false)

    render(<AutoArchiveRow days={30} onCommit={onCommit} saved={false} />)
    await screen.findByText('none stale now')

    const input = screen.getByRole('spinbutton', { name: 'Auto-archive after (days)' })
    fireEvent.change(input, { target: { value: '7' } })
    fireEvent.blur(input)

    await waitFor(() => expect(input).toHaveValue(30))
    expect(screen.queryByText('7 stale now')).toBeNull()
  })

  it('does not show a count returned for a different threshold', async () => {
    vi.spyOn(api, 'autoArchiveSessions').mockResolvedValue(result(14, 9))

    render(<AutoArchiveRow days={30} onCommit={async () => true} saved={false} />)

    await waitFor(() => expect(api.autoArchiveSessions).toHaveBeenCalled())
    expect(screen.queryByText('9 stale now')).toBeNull()
  })
})
