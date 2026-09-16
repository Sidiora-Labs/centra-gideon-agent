import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ExternalAccess } from '../../shared/data/api'
import { ExternalAccessPanel } from './ExternalAccessPanel'


const externalAccess = vi.fn()
const patchConfig = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    externalAccess: (...a: unknown[]) => externalAccess(...a),
    patchConfig: (...a: unknown[]) => patchConfig(...a),
    externalAccessCreateClient: vi.fn(),
    externalAccessRevokeClient: vi.fn(),
    externalAccessSetClientDisabled: vi.fn(),
  },
}))

const STATE: ExternalAccess = {
  enabled: true,
  incident_active: false,
  public_url: '',
  caps: {
    rate_rps: 1,
    rate_burst: 20,
    rate_concurrent: 4,
    auto_disable_after_breaches: 10,
    capture_retention_days: 30,
    capture_upstream_allowlist: ['api.openai.com'],
  },
  surfaces: [
    {
      surface: 'mcp',
      enabled: true,
      allow_remote: false,
      token_configured: true,
      token_problem: '',
      loopback_only: false,
    },
    {
      surface: 'a2a',
      enabled: false,
      allow_remote: false,
      token_configured: false,
      token_problem: 'no token configured (run: gideon inbound token create a2a)',
      loopback_only: false,
    },
  ],
  clients: [],
}

describe('the external-access controls reach the backend', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    externalAccess.mockResolvedValue(STATE)
    patchConfig.mockResolvedValue({})
  })

  it('renders every cap the endpoint reports — none of them is a silent payload field', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText('Requests per second')).toBeTruthy())
    expect((screen.getByLabelText('Requests per second') as HTMLInputElement).value).toBe('1')
    expect((screen.getByLabelText('Burst') as HTMLInputElement).value).toBe('20')
    expect((screen.getByLabelText('Concurrent requests') as HTMLInputElement).value).toBe('4')
    expect((screen.getByLabelText('Switch a client off after') as HTMLInputElement).value).toBe('10')
    expect(
      (screen.getByLabelText('Keep captured sessions for (days)') as HTMLInputElement).value,
    ).toBe('30')
  })


  it('renders the upstream allow-list the endpoint reports', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText('Add to capture upstream allow-list')).toBeTruthy())
    expect(screen.getByText('api.openai.com')).toBeTruthy()
    expect(screen.getByLabelText('Remove api.openai.com')).toBeTruthy()
  })

  it('adding a host PATCHes external_access.capture.upstream_allowlist with the WHOLE list', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText('Add to capture upstream allow-list')).toBeTruthy())
    await userEvent.type(
      screen.getByLabelText('Add to capture upstream allow-list'),
      'api.anthropic.com{Enter}',
    )
    await waitFor(() =>
      expect(patchConfig).toHaveBeenCalledWith('external_access.capture.upstream_allowlist', [
        'api.openai.com',
        'api.anthropic.com',
      ]),
    )
    expect(
      patchConfig.mock.calls.filter((c) => c[0] === 'external_access.capture_retention_days'),
    ).toEqual([])
  })

  it('removing a host PATCHes the remaining list, and the value round-trips back into the pane', async () => {
    externalAccess.mockResolvedValueOnce(STATE).mockResolvedValue({
      ...STATE,
      caps: { ...STATE.caps, capture_upstream_allowlist: [] },
    })
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText('Remove api.openai.com')).toBeTruthy())
    await userEvent.click(screen.getByLabelText('Remove api.openai.com'))
    await waitFor(() =>
      expect(patchConfig).toHaveBeenCalledWith('external_access.capture.upstream_allowlist', []),
    )
    await waitFor(() => expect(screen.queryByText('api.openai.com')).toBeNull())
  })

  it('the MASTER switch PATCHes external_access.enabled', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText('Allow inbound access')).toBeTruthy())
    await userEvent.click(screen.getByLabelText('Allow inbound access'))
    await waitFor(() =>
      expect(patchConfig).toHaveBeenCalledWith('external_access.enabled', false),
    )
  })

  it('a surface switch PATCHes that surface, not the master', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText(/MCP tool surface/i)).toBeTruthy())
    await userEvent.click(screen.getByLabelText(/MCP tool surface/i))
    await waitFor(() =>
      expect(patchConfig).toHaveBeenCalledWith('external_access.mcp.enabled', false),
    )
    expect(patchConfig).not.toHaveBeenCalledWith('external_access.enabled', expect.anything())
  })

  it('each cap PATCHes its OWN key', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText('Burst')).toBeTruthy())
    const burst = screen.getByLabelText('Burst') as HTMLInputElement
    await userEvent.clear(burst)
    await userEvent.type(burst, '25{Enter}')
    await waitFor(() =>
      expect(patchConfig.mock.calls.some((c) => c[0] === 'external_access.rate_burst')).toBe(true),
    )
    const otherCaps = [
      'external_access.rate_rps',
      'external_access.rate_concurrent',
      'external_access.auto_disable_after_breaches',
      'external_access.capture_retention_days',
    ]
    expect(patchConfig.mock.calls.filter((c) => otherCaps.includes(c[0] as string))).toEqual([])
  })

  it('says the public URL is not editable here rather than just omitting the control', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() =>
      expect(screen.getByText(/not set — every surface is loopback-only/i)).toBeTruthy(),
    )
    expect(screen.getAllByText(/not editable here/i).length).toBeGreaterThan(0)
  })
})
