import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

const CFG = { companion: { discovery_enabled: true, instance_name: 'Living room Mac' } }

const discovery = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    gideonConfig: () => Promise.resolve(CFG),
    pushStatus: () => Promise.resolve({ backend: 'webpush', vapid_public_key: 'k', vapid_ready: true, devices: [] }),
    patchConfig: () => Promise.resolve({}),
    companionDiscovery: () => discovery(),
  },
}))

const ADVERTISING = {
  advertising: true,
  reason: 'advertising',
  detail: 'Advertising on the local network.',
  service_type: '_gideon._tcp.local.',
  instance_name: 'Living room Mac',
  port: 10166,
  addresses: ['192.168.1.37'],
  txt: { name: 'Living room Mac', port: '10166', requires_pairing: '1', schema: '1' },
}

const LOOPBACK = {
  advertising: false,
  reason: 'loopback_only',
  detail:
    'LAN discovery is on, but this gateway is bound to loopback only, so nothing on your ' +
    'network could reach it. Not advertising.',
  service_type: '_gideon._tcp.local.',
  instance_name: '',
  port: 0,
  addresses: [],
  txt: {},
}

describe('the Companion panel reports what LAN discovery is actually doing', () => {
  beforeEach(() => { discovery.mockReset() })

  it('says Advertising, in words, when the advertiser is live', async () => {
    discovery.mockResolvedValue(ADVERTISING)
    const { CompanionPanel } = await import('./CompanionPanel')
    render(<CompanionPanel />)
    expect(await screen.findByText('Advertising')).toBeTruthy()
  })

  it('shows the record verbatim, so the owner can read what the network is told', async () => {
    discovery.mockResolvedValue(ADVERTISING)
    const { CompanionPanel } = await import('./CompanionPanel')
    render(<CompanionPanel />)
    expect(await screen.findByText('_gideon._tcp.local.')).toBeTruthy()
    expect(screen.getByText('192.168.1.37:10166')).toBeTruthy()
    expect(screen.getByText('requires_pairing')).toBeTruthy()
    expect(screen.getByText(/no token, no session and no content/i)).toBeTruthy()
  })

  it('says Not advertising AND why, when the toggle is on but the bind is loopback-only', async () => {
    discovery.mockResolvedValue(LOOPBACK)
    const { CompanionPanel } = await import('./CompanionPanel')
    render(<CompanionPanel />)
    expect(await screen.findByText('Not advertising')).toBeTruthy()
    expect(screen.getByText(/bound to loopback only/)).toBeTruthy()
    expect(screen.queryByText('_gideon._tcp.local.')).toBeNull()
  })

  it('renders the backend sentence rather than mapping the reason code itself', () => {
    const code = read('features/settings/CompanionPanel.tsx')
    expect(code).toMatch(/hint=\{discovery\.detail\}/)
    expect(code, 'no local copy of the reason wording').not.toMatch(/loopback_only/)
  })

  it('states the status in words, so it is not carried by colour alone (1.4.1)', () => {
    const code = read('features/settings/CompanionPanel.tsx')
    expect(code).toMatch(/'Advertising' : 'Not advertising'/)
  })

  it('re-reads the live state after the toggle is PATCHed', () => {
    const code = read('features/settings/CompanionPanel.tsx')
    expect(code).toMatch(/refreshDiscovery\(\)/)
  })
})
