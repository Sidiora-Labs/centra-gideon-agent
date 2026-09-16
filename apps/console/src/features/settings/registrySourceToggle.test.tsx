import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { AppsPanel } from './AppsPanel'


const patchConfig = vi.fn((_path: string, _value: unknown) => Promise.resolve({}))
const gideonConfig = vi.fn()
const apps = vi.fn()

vi.mock('../../shared/data/api', () => ({
  api: {
    gideonConfig: () => gideonConfig(),
    patchConfig: (path: string, value: unknown) => patchConfig(path, value),
    apps: () => apps(),
  },
}))
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))
vi.mock('../apps/appConfigForm', () => ({
  AppConfigFields: () => null,
  useAppConfig: () => ({ savedAt: 0, dirty: false, error: null, reload: () => {}, fields: [] }),
}))
vi.mock('../../shared/data/data', () => ({
  useQuery: (_k: string, fn: () => Promise<unknown>) => {
    const [data, setData] = useState<unknown>(null)
    const [error, setError] = useState<unknown>(null)
    useEffect(() => { fn().then(setData).catch(setError) }, [])
    return { data, error, refresh: () => {} }
  },
  invalidateKeys: () => {},
}))

const NAME = /curated app registry/i

describe('the registry-source switch in Settings > Apps', () => {
  beforeEach(() => {
    patchConfig.mockClear()
    gideonConfig.mockResolvedValue({ apps: { registry_source_enabled: true } })
    apps.mockResolvedValue([])
  })

  it('renders with an accessible name', async () => {
    render(<AppsPanel />)
    const sw = await waitFor(() => screen.getByRole('switch', { name: NAME }))
    expect(sw).toBeTruthy()
  })

  it('reads the saved value rather than a fallback', async () => {
    gideonConfig.mockResolvedValue({ apps: { registry_source_enabled: false } })
    render(<AppsPanel />)
    const sw = await waitFor(() => screen.getByRole('switch', { name: NAME }))
    expect(sw.getAttribute('aria-checked')).toBe('false')
  })

  it('PATCHes the allowlisted dotted path when flipped', async () => {
    render(<AppsPanel />)
    const sw = await waitFor(() => screen.getByRole('switch', { name: NAME }))
    expect(sw.getAttribute('aria-checked')).toBe('true')
    fireEvent.click(sw)
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('apps.registry_source_enabled', false))
  })

  it('renders even while the installed-apps list is still loading', async () => {
    apps.mockReturnValue(new Promise(() => {}))
    render(<AppsPanel />)
    const sw = await waitFor(() => screen.getByRole('switch', { name: NAME }))
    expect(sw).toBeTruthy()
  })

  it('a failed config read renders the failure, not a switch at its fallback', async () => {
    gideonConfig.mockRejectedValue(new Error('boom'))
    render(<AppsPanel />)
    await waitFor(() => expect(screen.queryAllByText(/app store settings/i).length).toBeGreaterThan(0))
    expect(screen.queryByRole('switch', { name: NAME })).toBeNull()
  })
})
