import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { AgentDefaultsPanel } from './AgentDefaultsPanel'


const agentRunners = vi.fn()
const gideonConfig = vi.fn()

const VERBATIM = "'gemini' not found on PATH (looked for: gemini); set GEMINI_CLI_EXECUTABLE to override"

vi.mock('../../shared/data/api', () => ({
  api: {
    agentRunners: (probe?: boolean) => agentRunners(probe),
    gideonConfig: () => gideonConfig(),
    patchConfig: () => Promise.resolve({}),
    agents: () => Promise.resolve({ default_agent: '' }),
    setDefaultAgent: () => Promise.resolve({}),
  },
}))
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../shared/data/agents', () => ({
  useAgentCatalog: () => ({ options: [], loading: false, discovered: [] }),
  ensureBindableAgentName: (v: string) => Promise.resolve(v),
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

const unhealthyRow = {
  id: 'gemini-cli', display_name: 'Gemini CLI', runtime_id: 'acp:gemini-cli', source: 'builtin',
  dialect: '', bin_names: ['gemini'],
  health: {
    ok: false, probe: 'path', checked_at: '2026-08-17T10:00:00+00:00',
    version: null, latency_ms: null, error: VERBATIM, resolved_command: [],
  },
  health_stale: false,
  capabilities: null,
  adapter: { npm_pkg: '', pinned: false, state: 'no_adapter', verified: true, detail: 'launches its own binary' },
  lease: null,
}

const healthyRow = {
  id: 'claude-code', display_name: 'Claude Code', runtime_id: 'acp:claude-code', source: 'builtin',
  dialect: 'claude-code', bin_names: ['claude'],
  health: {
    ok: true, probe: 'version', checked_at: '2026-08-17T10:00:00+00:00',
    version: '2.1.233', latency_ms: 58, error: null, resolved_command: ['/usr/bin/claude'],
  },
  health_stale: false,
  capabilities: {
    source: 'initialize', recorded_at: '2026-08-17T10:00:00+00:00',
    models: ['m1', 'm2'], permission_modes: ['default', 'acceptEdits'], efforts: ['low', 'high'],
  },
  adapter: { npm_pkg: '@x/adapter', pinned: false, state: 'unverified', verified: false, detail: 'no recorded provenance' },
  lease: null,
}

describe('the runner rows in Settings → Agent defaults', () => {
  beforeEach(() => {
    gideonConfig.mockResolvedValue({ agent: { unattended_requires_verified_adapter: false } })
    agentRunners.mockResolvedValue([healthyRow, unhealthyRow])
  })

  it('prints the probe error verbatim, not a house paraphrase', async () => {
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText(VERBATIM)).toBeTruthy())
  })

  it('renders an unmeasured latency and version as unknown, never as a number', async () => {
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText('latency unknown')).toBeTruthy())
    expect(screen.getByText('version unknown')).toBeTruthy()
    expect(screen.queryByText('0 ms')).toBeNull()
  })

  it('shows measured evidence and capability chips for a healthy runner', async () => {
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText('v2.1.233')).toBeTruthy())
    expect(screen.getByText('58 ms')).toBeTruthy()
    expect(screen.getByText('acceptEdits')).toBeTruthy()
    expect(screen.getByText('2 models')).toBeTruthy()
  })

  it('marks an overdue check without contradicting the reading itself', async () => {
    agentRunners.mockResolvedValue([{ ...healthyRow, health_stale: true }])
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText('check overdue')).toBeTruthy())
    expect(screen.getByText('healthy')).toBeTruthy()
  })

  it('does not mark a fresh or unknown-age check overdue', async () => {
    agentRunners.mockResolvedValue([healthyRow, { ...unhealthyRow, health_stale: null }])
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText('healthy')).toBeTruthy())
    expect(screen.queryByText('check overdue')).toBeNull()
  })

  it('says capabilities are unknown when no handshake was ever recorded', async () => {
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText(/Capabilities unknown/)).toBeTruthy())
  })

  it('surfaces each row adapter verdict so the gate is legible before it fires', async () => {
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText('adapter unverified')).toBeTruthy())
    expect(screen.getByText('no recorded provenance')).toBeTruthy()
  })

  it('renders the failure instead of an empty runner list when the read fails', async () => {
    agentRunners.mockRejectedValue(new Error('boom'))
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText(/Couldn't load your runners/i)).toBeTruthy())
    expect(screen.queryByText(/No runners in the catalog/)).toBeNull()
  })


  const held = {
    holder: 'chat:web:alice', taken_at: 0, expires_at: 0, renewals: 2,
    age_secs: 42, expires_in_secs: 1758,
  }

  it('names the session holding a runner, and says when idle-release takes it back', async () => {
    agentRunners.mockResolvedValue([{ ...healthyRow, lease: held }, unhealthyRow])
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText('held by chat:web:alice')).toBeTruthy())
    expect(screen.getByText('held for 42s')).toBeTruthy()
    expect(screen.getByText('released in 1758s if idle')).toBeTruthy()
    expect(screen.getByText('healthy')).toBeTruthy()
  })

  it('shows no holder for a free runner', async () => {
    agentRunners.mockResolvedValue([healthyRow, unhealthyRow])
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText('healthy')).toBeTruthy())
    expect(screen.queryByText(/held by/)).toBeNull()
    expect(screen.queryByText(/released in/)).toBeNull()
  })
})
