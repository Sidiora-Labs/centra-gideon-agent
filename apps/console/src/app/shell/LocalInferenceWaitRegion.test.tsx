import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { LocalInferenceWaitRegion } from './LocalInferenceWaitRegion'

const seam = vi.hoisted(() => ({ move: vi.fn(), refresh: vi.fn(), waits: [{ id: 'queued', step: 'Answering your chat', model: 'regular:8b', provider: 'Local', holder: 'background work', position: 1, next_ref: 'Other:next', seconds_left: 14.1 }] }))
vi.mock('../../shared/data/localInference', () => ({ useLocalInferenceWaits: () => ({ waits: seam.waits, refresh: seam.refresh }) }))
vi.mock('../../shared/data/api', () => ({ api: { localInferenceMoveOn: seam.move } }))
vi.mock('./appSdk', () => ({ notify: vi.fn() }))

describe('local inference wait notice', () => {
  it('shows authored waiting reason and moves only the selected queued request', async () => {
    seam.move.mockResolvedValue({ moved: true })
    render(<LocalInferenceWaitRegion />)
    expect(screen.getByText(/Waiting for regular:8b on Local/)).toHaveTextContent('background work')
    expect(screen.getByText(/15 seconds left/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Try next model' }))
    await waitFor(() => expect(seam.move).toHaveBeenCalledWith('queued'))
    await waitFor(() => expect(seam.refresh).toHaveBeenCalled())
  })
  it('offers no move-on for a pinned request', () => {
    seam.waits = [{ ...seam.waits[0], next_ref: '' }]
    render(<LocalInferenceWaitRegion />)
    expect(screen.queryByRole('button', { name: 'Try next model' })).not.toBeInTheDocument()
  })
})
