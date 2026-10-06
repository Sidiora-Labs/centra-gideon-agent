import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ModelTest } from './ModelTest'
import type { AvailableModel } from '../../shared/data/api'
const seam = vi.hoisted(() => ({ test: vi.fn() }))
vi.mock('../../shared/data/api', () => ({ api: { modelTest: seam.test } }))
const model: AvailableModel = { id: 'chosen:8b', name: 'Chosen model', provider: 'Registered', provider_type: 'ollama', capabilities: ['chat'] }

describe('per-use-case model Test', () => {
  it('tests the row model and its actual use case on click even without a downloaded flag', async () => {
    seam.test.mockResolvedValue({ ok: true, detail: 'Replied “OK”.', reason: '', duration_ms: 2 })
    render(<ModelTest useCase="reasoning" model={model} />)
    expect(seam.test).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Test Chosen model for reasoning' }))
    await waitFor(() => expect(seam.test).toHaveBeenCalledWith('reasoning', 'Registered:chosen:8b'))
    expect(await screen.findByRole('status')).toHaveTextContent('Replied “OK”.')
  })
  it('shows provider refusal and has no invalid local-registry Test on a hosted media row', () => {
    render(<ModelTest useCase="image_gen" model={{ ...model, downloaded: true, capabilities: ['image_gen'], untestable: { image_gen: 'No small image Test is available yet.' } }} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByText('No Test: No small image Test is available yet.')).toBeInTheDocument()
  })
  it('keeps a stale answer off a different model row', async () => {
    let resolve!: (value: unknown) => void
    seam.test.mockReturnValue(new Promise(r => { resolve = r }))
    const view = render(<ModelTest useCase="chat" model={model} />)
    fireEvent.click(screen.getByRole('button', { name: 'Test Chosen model for chat' }))
    view.rerender(<ModelTest useCase="chat" model={{ ...model, id: 'next:8b', name: 'Next model' }} />)
    resolve({ ok: true, detail: 'OLD RESPONSE', reason: '', duration_ms: 1 })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Test Next model for chat' })).not.toBeDisabled())
    expect(screen.queryByText('OLD RESPONSE')).not.toBeInTheDocument()
  })
})
