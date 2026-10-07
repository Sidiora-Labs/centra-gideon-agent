import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { AgentExportPreview, SavedAgent } from '../../shared/data/api'

const localAgent: SavedAgent = { name: 'research-notes', source: 'local', provider: 'native', description: 'Research summaries' }
const gideonAgent: SavedAgent = { name: 'release-helper', source: 'gideon', provider: 'native' }
const defaultAgent: SavedAgent = { name: 'daily-driver', source: 'local', provider: 'native' }
const builtIn: SavedAgent = { name: 'gideon-lite', source: 'builtin', provider: 'native', reserved: true }
const marketplace: SavedAgent = { name: 'market-agent', source: 'marketplace', provider: 'native' }
let catalog: { agents: SavedAgent[]; default_agent: string; agent_export_enabled: boolean }
const previewAgentExport = vi.fn<(body: { agents: string[]; destination?: string }) => Promise<AgentExportPreview>>()
const writeAgentExport = vi.fn<(token: string) => Promise<{ ok: true; files: string[]; unchanged: string[] }>>()

async function mountList() {
  const { AgentsListPage } = await import('./AgentsListPage')
  render(<AgentsListPage query={{}} setQuery={() => {}} onCreate={() => {}} />)
}

beforeEach(() => {
  vi.resetModules()
  catalog = { agents: [localAgent, gideonAgent, defaultAgent, builtIn, marketplace], default_agent: 'daily-driver', agent_export_enabled: false }
  previewAgentExport.mockReset()
  writeAgentExport.mockReset()
  vi.doMock('../../shared/data/api', async original => ({
    ...(await original<Record<string, unknown>>()),
    api: {
      agents: async () => catalog,
      agentProviders: async () => [],
      syncAgents: async () => ({ ok: true }),
      previewAgentExport,
      writeAgentExport,
    },
  }))
})

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('Claude Code agent export', () => {
  it('keeps the action hidden when the server says the console is hosted', async () => {
    await mountList()
    await screen.findByRole('button', { name: 'research-notes' })
    expect(screen.queryByRole('button', { name: 'Export to Claude Code' })).not.toBeInTheDocument()
  })

  it('offers only owner-created non-default agents and waits for the confirmed write response', async () => {
    catalog.agent_export_enabled = true
    const preview: AgentExportPreview = {
      format: 'claude-code-agents', installKind: 'per-agent', dest: '~/.claude/agents/{slug}.md',
      destination: '/home/operator/.claude/agents', files: [
        { path: 'research-notes.md', bytes: 180, status: 'new' },
        { path: 'release-helper.md', bytes: 112, status: 'new' },
      ], blocked: [], conflicts: [], preview_token: 'short-lived-preview-token', expires_in: 300,
    }
    previewAgentExport.mockResolvedValue(preview)
    let confirmWrite: ((value: { ok: true; files: string[]; unchanged: string[] }) => void) | undefined
    writeAgentExport.mockImplementation(() => new Promise(resolve => { confirmWrite = resolve }))
    await mountList()
    await userEvent.click(await screen.findByRole('button', { name: 'Export to Claude Code' }))
    const dialog = await screen.findByRole('dialog', { name: 'Export agents to Claude Code' })
    expect(screen.getByRole('checkbox', { name: /research-notes/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /release-helper/ })).toBeChecked()
    expect(screen.queryByRole('checkbox', { name: /daily-driver|gideon-lite|market-agent/ })).toBeNull()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Preview export' }))
    expect(await screen.findByText('research-notes.md', { exact: false })).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Export files' }))
    expect(screen.queryByText(/Export complete/)).not.toBeInTheDocument()
    expect(writeAgentExport).toHaveBeenCalledWith('short-lived-preview-token')
    confirmWrite?.({ ok: true, files: ['research-notes.md', 'release-helper.md'], unchanged: [] })
    await waitFor(() => expect(within(dialog).getByText(/^Export complete\./)).toHaveAttribute('role', 'status'))
  })
})
