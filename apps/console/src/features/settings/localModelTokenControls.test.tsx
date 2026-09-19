import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { api, type AvailableModel, type LocalModelTokenStatus } from '../../shared/data/api'
import { LocalModelManager, gatedModelAccess, localModelTestDetail } from './LocalModelManager'
import { ModelTokenSection } from './ModelsPanel'

vi.mock('../../shared/data/data', () => ({
  useQuery: (_key: string, fn: () => Promise<unknown>) => {
    const [data, setData] = useState<unknown>()
    const [error, setError] = useState<unknown>()
    const load = () => fn().then(setData).catch(setError)
    useEffect(() => { void load() }, [])
    return { data, error, refresh: load }
  },
  invalidateKeys: vi.fn(),
}))

const TOKEN_READY: LocalModelTokenStatus = {
  configured: true,
  valid: true,
  source: 'credential_store',
  masked_token: 'hf_…1234',
  state: 'valid',
  username: 'octo',
  error: '',
  cached: false,
  checked_at: 1,
  expires_at: 2,
}

const TOKEN_MISSING: LocalModelTokenStatus = {
  configured: false,
  valid: null,
  source: 'none',
  masked_token: '',
  state: 'unconfigured',
  username: '',
  error: '',
  cached: false,
  checked_at: 0,
  expires_at: 0,
}

const GATED: AvailableModel = {
  id: 'gated-model',
  name: 'gated-model',
  capabilities: ['stt'],
  provider: 'faster-whisper',
  provider_type: 'local',
  downloaded: false,
  gated: true,
}

beforeEach(() => {
  ;(globalThis as unknown as { EventSource: unknown }).EventSource = class {
    close() {}
    addEventListener() {}
    onerror: unknown = null
  }
  vi.spyOn(api, 'modelDownloads').mockResolvedValue([])
  vi.spyOn(api, 'downloadStreamUrl').mockReturnValue('/api/models/downloads/job/stream')
  vi.spyOn(api, 'localModelTokenStatus').mockResolvedValue(TOKEN_MISSING)
})

afterEach(() => vi.restoreAllMocks())

describe('gated-model pre-warnings', () => {
  it('blocks the download before a validated token is ready and points to Models', async () => {
    render(<LocalModelManager provider="faster-whisper" models={[GATED]} onChanged={vi.fn()} />)
    const download = screen.getByRole('button', { name: 'Download gated-model' })
    expect(download.hasAttribute('disabled')).toBe(true)
    expect(await screen.findByText(/Add a valid Hugging Face token in Models/i)).toBeTruthy()
    expect(gatedModelAccess(GATED, false)).toBe('token-required')
  })

  it('allows a token-ready gated download while warning that model access is separate', async () => {
    const start = vi.spyOn(api, 'startModelDownload').mockResolvedValue({ id: 'job' } as never)
    vi.mocked(api.localModelTokenStatus).mockResolvedValue(TOKEN_READY)
    render(<LocalModelManager provider="faster-whisper" models={[GATED]} onChanged={vi.fn()} />)
    const download = await screen.findByRole('button', { name: 'Download gated-model' })
    await waitFor(() => expect(download.hasAttribute('disabled')).toBe(false))
    expect(download.hasAttribute('disabled')).toBe(false)
    expect(screen.getByText(/accept its access terms on Hugging Face/i)).toBeTruthy()
    fireEvent.click(download)
    await waitFor(() => expect(start).toHaveBeenCalledWith('faster-whisper', 'gated-model'))
  })
})

describe('inline local-provider Test control', () => {
  it('runs the provider self-test and surfaces its typed per-capability failure', async () => {
    const test = vi.spyOn(api, 'testLocalModelProvider').mockResolvedValue({
      provider: 'faster-whisper',
      ok: false,
      tests: [
        {
          capability: 'stt',
          ok: false,
          detail: 'probe failed',
          elapsed_ms: 1,
          failure: { code: 'unavailable', message: 'Download weights first.', retryable: false },
        },
      ],
      failure: null,
    })
    render(<LocalModelManager provider="faster-whisper" models={[GATED]} onChanged={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Test' }))
    await waitFor(() => expect(test).toHaveBeenCalledWith('faster-whisper'))
    expect(await screen.findByText(/stt: Download weights first\. \(unavailable\)/i)).toBeTruthy()
  })

  it('keeps an honest empty-result message instead of claiming an unrun capability passed', () => {
    expect(localModelTestDetail({ provider: 'p', ok: false, tests: [], failure: null }))
      .toEqual(['No capability checks completed.'])
  })

  it('surfaces a typed provider-level refusal such as the serialized busy response', () => {
    expect(localModelTestDetail({
      provider: 'p', ok: false, tests: [],
      failure: { code: 'busy', message: 'A self-test is already running.', retryable: true },
    })).toEqual(['A self-test is already running.'])
  })
})

describe('Models token management', () => {
  it('shows masked status without putting a token value into the password field', async () => {
    vi.mocked(api.localModelTokenStatus).mockResolvedValue(TOKEN_READY)
    render(<ModelTokenSection />)
    expect(await screen.findByText(/Saved in Gideon for octo · hf_…1234/)).toBeTruthy()
    expect((screen.getByLabelText('Hugging Face token') as HTMLInputElement).value).toBe('')
    expect((screen.getByLabelText('Hugging Face token') as HTMLInputElement).type).toBe('password')
  })

  it('saves a replacement through the dedicated token endpoint and clears the draft', async () => {
    vi.mocked(api.localModelTokenStatus).mockResolvedValue(TOKEN_READY)
    const save = vi.spyOn(api, 'saveLocalModelToken').mockResolvedValue(TOKEN_READY)
    render(<ModelTokenSection />)
    const input = await screen.findByLabelText('Hugging Face token') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'hf_new-secret' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save token' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith('hf_new-secret'))
    expect(input.value).toBe('')
  })

  it('tests the effective cascade inline and reports the validated identity', async () => {
    vi.mocked(api.localModelTokenStatus).mockResolvedValue(TOKEN_READY)
    const test = vi.spyOn(api, 'testLocalModelToken').mockResolvedValue(TOKEN_READY)
    render(<ModelTokenSection />)
    const button = await screen.findByRole('button', { name: 'Test' })
    fireEvent.click(button)
    await waitFor(() => expect(test).toHaveBeenCalledOnce())
    expect(await screen.findByText(/Token works\. Saved in Gideon for octo · hf_…1234/)).toBeTruthy()
  })
})
