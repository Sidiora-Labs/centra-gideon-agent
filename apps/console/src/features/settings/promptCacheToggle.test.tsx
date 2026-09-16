import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { ModelsPanel } from './ModelsPanel'


const patchConfig = vi.fn((_path: string, _value: unknown) => Promise.resolve({}))
const gideonConfig = vi.fn()

vi.mock('../../shared/data/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      gideonConfig: () => gideonConfig(),
      patchConfig: (path: string, value: unknown) => patchConfig(path, value),
      modelsAvailable: () => Promise.resolve([]),
      modelsActive: () => Promise.resolve({}),
      modelsHealth: () => Promise.resolve({ providers: [] }),
      judgeBench: () => Promise.reject(new actual.ApiError('No judge benchmark has run yet. Run `gideon judge-bench` to produce one.', 404, 'judge_bench_absent')),
      modelDownloadCleanupCandidates: () => Promise.resolve({ candidates: [], reclaimable_bytes: 0 }),
      modelsLoaded: () => Promise.resolve({
        loaded: [],
        providers: [],
        pressure: { total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, warn_pct: 85, warn: false, source: 'unavailable' },
      }),
    },
  }
})
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../shared/data/data', () => ({
  useQuery: (_k: string, fn: () => Promise<unknown>) => {
    const [data, setData] = useState<unknown>(null)
    const [error, setError] = useState<unknown>(null)
    useEffect(() => { fn().then(setData).catch(setError) }, [])
    return { data, error, refresh: () => {} }
  },
  invalidateKeys: () => {},
}))

describe('the prompt-cache switch in the Models panel', () => {
  beforeEach(() => {
    patchConfig.mockClear()
    gideonConfig.mockResolvedValue({ agent: { prompt_cache_enabled: true } })
  })

  it('renders in the Models panel with an accessible name', async () => {
    render(<ModelsPanel />)
    const sw = await waitFor(() => screen.getByRole('switch', { name: /prompt caching/i }))
    expect(sw).toBeTruthy()
  })

  it('reads the saved value rather than a fallback', async () => {
    gideonConfig.mockResolvedValue({ agent: { prompt_cache_enabled: false } })
    render(<ModelsPanel />)
    const sw = await waitFor(() => screen.getByRole('switch', { name: /prompt caching/i }))
    expect(sw.getAttribute('aria-checked')).toBe('false')
  })

  it('PATCHes the allowlisted dotted path when flipped', async () => {
    render(<ModelsPanel />)
    const sw = await waitFor(() => screen.getByRole('switch', { name: /prompt caching/i }))
    expect(sw.getAttribute('aria-checked')).toBe('true')
    fireEvent.click(sw)
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('agent.prompt_cache_enabled', false))
  })

  it('a failed config read renders the failure, not a switch at its fallback', async () => {
    gideonConfig.mockRejectedValue(new Error('boom'))
    render(<ModelsPanel />)
    await waitFor(() => expect(screen.queryAllByText(/prompt-cache setting/i).length).toBeGreaterThan(0))
    expect(screen.queryByRole('switch', { name: /prompt caching/i })).toBeNull()
  })
})
