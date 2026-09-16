import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CompanionPanel } from './CompanionPanel'
import { invalidateKeys } from '../../shared/data/data'


const patchConfig = vi.fn()
const config = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    gideonConfig: () => config(),
    patchConfig: (path: string, value: unknown) => patchConfig(path, value),
    companionDiscovery: () => Promise.resolve({ advertising: false, reason: 'off', detail: 'Off.', service_type: '', instance_name: '', port: 0, addresses: [], txt: {} }),
    pushStatus: () => pushStatusResult(),
  },
}))
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))

let pushStatusResult = () =>
  Promise.resolve({ backend: 'webpush', vapid_public_key: 'k', vapid_ready: true, devices: [] })

const cfgWith = (mobile: Record<string, unknown>) => ({
  companion: { discovery_enabled: false, instance_name: '' },
  mobile,
})

beforeEach(() => {
  patchConfig.mockReset().mockResolvedValue({})
  config.mockReset()
  invalidateKeys('settings:companion')
  invalidateKeys('settings:companion:mobile')
  sessionStorage.clear()
})
afterEach(cleanup)

describe('the phone-push settings control', () => {
  it('renders the three backends and PATCHes the `mobile.` path', async () => {
    config.mockResolvedValue(cfgWith({ push_backend: 'webpush', ntfy_topic_url: '' }))
    render(<CompanionPanel />)

    const ntfy = await screen.findByRole('button', { name: 'Push backend: ntfy' })
    expect(screen.getByRole('button', { name: 'Push backend: Web push' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Push backend: Off' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Push backend: Web push' }).getAttribute('aria-pressed')).toBe('true')

    await userEvent.click(ntfy)
    expect(patchConfig).toHaveBeenCalledWith('mobile.push_backend', 'ntfy')
  })

  it('only offers the topic URL for the backend that reads it', async () => {
    config.mockResolvedValue(cfgWith({ push_backend: 'webpush', ntfy_topic_url: '' }))
    render(<CompanionPanel />)
    await screen.findByRole('button', { name: 'Push backend: ntfy' })
    expect(screen.queryByPlaceholderText('https://ntfy.example/gideon')).toBeNull()

    cleanup()
    invalidateKeys('settings:companion')
    invalidateKeys('settings:companion:mobile')
    sessionStorage.clear()
    config.mockResolvedValue(cfgWith({ push_backend: 'ntfy', ntfy_topic_url: '' }))
    render(<CompanionPanel />)
    expect(await screen.findByPlaceholderText('https://ntfy.example/gideon')).toBeTruthy()
  })

  it('saves the topic URL under `mobile.ntfy_topic_url`, and only when it changed', async () => {
    config.mockResolvedValue(cfgWith({ push_backend: 'ntfy', ntfy_topic_url: '' }))
    render(<CompanionPanel />)
    const input = await screen.findByPlaceholderText('https://ntfy.example/gideon')

    const saves = () => screen.getAllByRole('button', { name: /save/i })
    expect(saves().every((b) => (b as HTMLButtonElement).getAttribute('aria-disabled') === 'true' || (b as HTMLButtonElement).disabled)).toBe(true)

    await userEvent.type(input, 'https://ntfy.example/mine')
    const enabled = saves().find((b) => !(b as HTMLButtonElement).disabled && (b as HTMLButtonElement).getAttribute('aria-disabled') !== 'true')
    expect(enabled, 'no Save became enabled after typing').toBeTruthy()
    await userEvent.click(enabled!)
    await waitFor(() =>
      expect(patchConfig).toHaveBeenCalledWith('mobile.ntfy_topic_url', 'https://ntfy.example/mine'),
    )
  })

  it('states the ids-only promise where the user chooses the transport', async () => {
    config.mockResolvedValue(cfgWith({ push_backend: 'webpush', ntfy_topic_url: '' }))
    render(<CompanionPanel />)
    await screen.findByRole('button', { name: 'Push backend: ntfy' })
    expect(screen.getByText(/ids only/i).textContent).toMatch(/never the tool/i)
  })

  it('renders defaults rather than crashing when the config has no `mobile` section', async () => {
    config.mockResolvedValue({ companion: { discovery_enabled: false, instance_name: '' } })
    render(<CompanionPanel />)
    const webpush = await screen.findByRole('button', { name: 'Push backend: Web push' })
    expect(webpush.getAttribute('aria-pressed')).toBe('true')
  })
})
