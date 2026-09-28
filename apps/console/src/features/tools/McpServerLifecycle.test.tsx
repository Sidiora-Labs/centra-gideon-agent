import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { EditToolServerModal, ImportSuggestions, buildMcpServerEditPatch } from './ToolsPage'
import type { ImportableMcpServer, McpServer } from '../../shared/data/api'

const remote: McpServer = {
  name: 'remote-tools', status: 'ready', tools: [], url: 'https://mcp.example.com/mcp',
  transport: 'streamable-http', headers: ['Authorization'],
  env: ['LOG_LEVEL'], allowed: false, allowRevision: 'revision-1', allowQuestion: 'Review access',
}

describe('MCP edit lifecycle preserves omitted values and saved headers', () => {
  it('keeps current endpoint and secret values out of edit inputs', () => {
    render(<EditToolServerModal server={remote} onClose={() => {}} onSaved={() => {}} />)
    expect(screen.getByPlaceholderText('https://mcp.example.com/mcp')).toHaveValue('')
    expect(screen.getByRole('tab', { name: 'Streamable HTTP' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByPlaceholderText('Authorization: Bearer token')).toHaveValue('')
    expect(document.body.textContent).not.toContain('Authorization: Bearer')
  })

  it('renders only safe display fields from an importable server record', () => {
    const candidate = {
      id: 'opaque-import-id', name: 'shared-tools', backend: 'windsurf', transport: 'streamable_http',
      display_command: '', display_args: [], display_url: 'https://mcp.example.com/mcp',
      env: [{ name: 'LOG_LEVEL', configured: true }], headers: [{ name: 'Authorization', configured: true }], secrets_skipped: 1,
      url: 'https://mcp.example.com/mcp?token=raw-secret', command: 'node', args: ['raw-secret'],
    } as ImportableMcpServer
    render(<ImportSuggestions servers={[candidate]} onImported={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /Discovered in other tools/ }))
    expect(document.body.textContent).toContain('https://mcp.example.com/mcp')
    expect(document.body.textContent).not.toContain('raw-secret')
  })

  it('does not repost projected URLs or header names when changing transport only', () => {
    expect(buildMcpServerEditPatch(remote, { mode: 'remote', transport: 'sse' })).toEqual({ transport: 'sse' })
  })

  it('sends only newly entered OSS header values', () => {
    expect(buildMcpServerEditPatch(remote, { mode: 'remote', headers: 'Authorization: Bearer replacement' })).toEqual({
      headers: { Authorization: 'Bearer replacement' },
    })
  })

  it('clears saved headers and environment settings only when explicitly requested', () => {
    expect(buildMcpServerEditPatch(remote, { mode: 'remote', clearHeaders: true, clearEnv: true })).toEqual({
      headers: {}, env: {},
    })
  })

  it('switches a local definition to remote with an explicit endpoint reset', () => {
    const local: McpServer = { name: 'local-tools', status: 'ready', tools: [], command: 'npx', args: ['old'], transport: 'stdio' }
    expect(buildMcpServerEditPatch(local, { mode: 'remote', url: 'https://mcp.example.com/mcp', transport: 'sse' })).toEqual({
      url: 'https://mcp.example.com/mcp', command: '', transport: 'sse',
    })
  })

  it('switches remote to local without copying the projected endpoint or arguments', () => {
    expect(buildMcpServerEditPatch(remote, { mode: 'stdio', command: 'npx', args: '-y package' })).toEqual({
      command: 'npx', url: '', args: ['-y', 'package'],
    })
  })

  it('refuses a no-op edit', () => {
    expect(buildMcpServerEditPatch(remote, { mode: 'remote' })).toBeNull()
  })
})
