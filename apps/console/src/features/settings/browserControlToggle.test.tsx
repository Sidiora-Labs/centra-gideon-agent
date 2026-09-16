import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'


const patchConfig = vi.fn((_path: string, _value: unknown) => Promise.resolve({}))
vi.mock('../../shared/data/api', () => ({
  api: {
    pushStatus: () => Promise.resolve({ backend: 'webpush', vapid_public_key: 'k', vapid_ready: true, devices: [] }),
    gideonConfig: () =>
      Promise.resolve({
        companion: { discovery_enabled: false, instance_name: '' },
        browse: { user_browser_enabled: false },
      }),
    patchConfig: (path: string, value: unknown) => patchConfig(path, value),
    companionDiscovery: () => Promise.resolve(null),
  },
}))

describe('the browser-control toggle writes the path it claims', () => {
  it('PATCHes browse.user_browser_enabled when flipped on', async () => {
    patchConfig.mockClear()
    const { CompanionPanel } = await import('./CompanionPanel')
    render(<CompanionPanel />)
    const toggle = await screen.findByRole('switch', { name: /let tasks drive my browser/i })
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    await userEvent.click(toggle)
    expect(patchConfig).toHaveBeenCalledWith('browse.user_browser_enabled', true)
  })

  it('states both limits beside the switch, not only in the docs', async () => {
    const { CompanionPanel } = await import('./CompanionPanel')
    render(<CompanionPanel />)
    await screen.findByRole('switch', { name: /let tasks drive my browser/i })
    expect(screen.getByText(/never switched to this machine's own browser profile/i)).toBeTruthy()
    expect(screen.getByText(/scheduled and unattended runs can never use your browser/i)).toBeTruthy()
  })
})
