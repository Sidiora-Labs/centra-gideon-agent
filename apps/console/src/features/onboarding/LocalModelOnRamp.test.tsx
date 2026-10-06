import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { LocalModelOnRamp, ModelProviderRecap } from './LocalModelOnRamp'
const seam = vi.hoisted(() => ({ setup: vi.fn(), scan: vi.fn(), invalidate: vi.fn(), refresh: vi.fn(), detection: { detected: true, endpoint: 'http://127.0.0.1:11434', model: 'tools-chat:8b', provider: '' }, providers: [{ name: 'Original', model: 'chosen:8b' }, { name: 'Local Ollama', model: 'tools-chat:8b' }] }))
vi.mock('../../shared/data/api', () => ({ api: { bindLocalModel: seam.setup, scanLocalModels: seam.scan, detectLocalModel: vi.fn(), modelProviders: vi.fn() } }))
vi.mock('../../shared/data/data', () => ({ useQuery: (key: string) => ({ data: key === 'onboarding:model-providers' ? seam.providers : seam.detection, refresh: seam.refresh }), invalidateKeys: seam.invalidate }))

describe('additional local provider onboarding', () => {
  it('offers a local model alongside ready chat, sends explicit add-only intent, and refreshes the real provider readers', async () => {
    seam.setup.mockResolvedValue({ ok: true, provider: 'Local Ollama', model: 'tools-chat:8b', status: 'added' })
    render(<LocalModelOnRamp bindChat={false} chatModel="Original:chosen:8b" />)
    expect(screen.getByText('Also add a local model')).toBeInTheDocument()
    expect(screen.getByText(/keeps Original:chosen:8b as your chat model/)).toBeInTheDocument()
    expect(seam.scan).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Add this model' }))
    await waitFor(() => expect(seam.setup).toHaveBeenCalledWith('http://127.0.0.1:11434', false))
    expect(await screen.findByText('Added as Local Ollama')).toBeInTheDocument()
    expect(seam.invalidate).toHaveBeenCalledWith('onboarding:model-providers')
    expect(seam.invalidate).toHaveBeenCalledWith('onboarding:local-model')
  })
  it('reads the server instance after reload and only scans on an explicit click', async () => {
    seam.detection = { ...seam.detection, provider: 'Local Ollama' }
    seam.scan.mockResolvedValue({ endpoints: [] })
    render(<LocalModelOnRamp bindChat={false} chatModel="Original:chosen:8b" />)
    expect(screen.getByText('Added as Local Ollama')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add this model' })).not.toBeInTheDocument()
    expect(seam.scan).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Scan my local network' }))
    await waitFor(() => expect(seam.scan).toHaveBeenCalledTimes(1))
    expect(await screen.findByText(/No model server answered the scan/)).toBeInTheDocument()
  })
  it('recaps every configured provider from the actual provider reader', () => {
    render(<ModelProviderRecap />)
    expect(screen.getByText('Original · chosen:8b')).toBeInTheDocument()
    expect(screen.getByText('Local Ollama · tools-chat:8b')).toBeInTheDocument()
  })
})
