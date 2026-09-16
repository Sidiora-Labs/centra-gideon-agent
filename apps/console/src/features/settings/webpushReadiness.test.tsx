
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import { CompanionPanel } from './CompanionPanel'
import { invalidateKeys } from '../../shared/data/data'

const cfgWith = (mobile: Record<string, unknown>) => ({
  companion: { discovery_enabled: false, instance_name: '' },
  mobile,
})

let configResult: () => Promise<unknown> = () => Promise.resolve(cfgWith({}))
let pushStatusResult = () =>
  Promise.resolve({ backend: 'webpush', vapid_public_key: '', vapid_ready: false, devices: [] })

vi.mock('../../shared/data/api', () => ({
  api: {
    gideonConfig: () => configResult(),
    patchConfig: () => Promise.resolve({}),
    companionDiscovery: () =>
      Promise.resolve({ advertising: false, reason: 'off', detail: 'Off.', service_type: '', instance_name: '', port: 0, addresses: [], txt: {} }),
    pushStatus: () => pushStatusResult(),
  },
}))
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../app/shell/registerServiceWorker', () => ({ serviceWorkerBlockedReason: () => null }))

afterEach(() => {
  cleanup()
})

beforeEach(() => {
  invalidateKeys('settings:companion')
  invalidateKeys('companion:push')
})

describe('the web push keypair readiness row', () => {
  it('says READY when the gateway holds a keypair', async () => {
    configResult = () => Promise.resolve(cfgWith({ push_backend: 'webpush' }))
    pushStatusResult = () =>
      Promise.resolve({ backend: 'webpush', vapid_public_key: 'pk', vapid_ready: true, devices: [] })
    render(<CompanionPanel />)
    await waitFor(() => expect(screen.getByText('Ready')).toBeTruthy())
    expect(screen.getByText(/subscribed devices can receive pushes/i)).toBeTruthy()
  })

  it('says NOT SET UP with the exact remedy when the keypair is missing', async () => {
    configResult = () => Promise.resolve(cfgWith({ push_backend: 'webpush' }))
    pushStatusResult = () =>
      Promise.resolve({ backend: 'webpush', vapid_public_key: '', vapid_ready: false, devices: [] })
    render(<CompanionPanel />)
    await waitFor(() => expect(screen.getByText('Not set up')).toBeTruthy())
    expect(screen.getAllByText(/gideon push init/).length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText(/Web push sends nothing/i)).toBeTruthy()
  })

  it('renders for the DEFAULT (unset) backend too — the first-run state is webpush', async () => {
    configResult = () => Promise.resolve(cfgWith({}))
    pushStatusResult = () =>
      Promise.resolve({ backend: 'webpush', vapid_public_key: '', vapid_ready: false, devices: [] })
    render(<CompanionPanel />)
    await waitFor(() => expect(screen.getByText('Not set up')).toBeTruthy())
  })

  it('is absent for backends that do not read it', async () => {
    for (const backend of ['ntfy', 'none']) {
      invalidateKeys('settings:companion')
  invalidateKeys('companion:push')
      configResult = () => Promise.resolve(cfgWith({ push_backend: backend }))
      pushStatusResult = () =>
        Promise.resolve({ backend, vapid_public_key: '', vapid_ready: false, devices: [] })
      render(<CompanionPanel />)
      await waitFor(() => expect(screen.getByText('Phone push')).toBeTruthy())
      expect(screen.queryByText('Not set up')).toBeNull()
      expect(screen.queryByText(/Keypair/)).toBeNull()
      cleanup()
    }
  })
})
