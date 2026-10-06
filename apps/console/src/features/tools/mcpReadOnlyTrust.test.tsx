import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ReadOnlyTrustControls } from './ReadOnlyTrustReview'
import { api, type McpServer } from '../../shared/data/api'

vi.mock('../../shared/data/api', () => ({ api: {
  trustMcpReadOnlyDefinitions: vi.fn(async () => ({ ok: true, sealed: ['lookup'], changedSince: [] })),
  revokeMcpReadOnlyTrust: vi.fn(async () => {}),
} }))
vi.mock('../../shared/data/data', () => ({ invalidateKeys: vi.fn() }))
const server: McpServer = { name: 'local-server', status: 'ok', allowed: true, tools: [{ name: 'lookup', description: '<script>untrusted text</script>', inputSchema: { type: 'object', properties: {} }, annotations: { readOnlyHint: true } }], readOnlyTrust: { trusted: false, listed: { lookup: 'reviewed-definition' }, configurationRevision: 'reviewed-config', catalogRevision: 'reviewed-catalog' } }
beforeEach(() => vi.clearAllMocks())
describe('MCP read-only definition review', () => {
  it('shows server text safely and grants the inventory the owner actually reviewed', async () => {
    const view = render(<ReadOnlyTrustControls server={server} />)
    fireEvent.click(screen.getByRole('button', { name: 'Review read-only labels' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('<script>untrusted text</script>')
    expect(screen.getByRole('dialog').querySelector('script')).toBeNull()
    expect(screen.getByText('Claims read-only')).toBeInTheDocument()
    view.rerender(<ReadOnlyTrustControls server={{ ...server, readOnlyTrust: { ...server.readOnlyTrust!, listed: { lookup: 'new-definition' }, catalogRevision: 'new-catalog' } }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Allow reviewed read-only definitions' }))
    await waitFor(() => expect(api.trustMcpReadOnlyDefinitions).toHaveBeenCalledWith('local-server', { lookup: 'reviewed-definition' }, 'reviewed-config', 'reviewed-catalog'))
  })
  it('keeps a stale review visible and reports the refusal without acknowledging trust', async () => {
    vi.mocked(api.trustMcpReadOnlyDefinitions).mockRejectedValueOnce(new Error('The reviewed definitions changed. Refresh and review again.'))
    render(<ReadOnlyTrustControls server={server} />)
    fireEvent.click(screen.getByRole('button', { name: 'Review read-only labels' }))
    fireEvent.click(screen.getByRole('button', { name: 'Allow reviewed read-only definitions' }))
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent('The reviewed definitions changed.'))
    expect(screen.getByRole('button', { name: 'Allow reviewed read-only definitions' })).toBeInTheDocument()
  })
  it('identifies changed parts and provides an immediate revoke', async () => {
    render(<ReadOnlyTrustControls server={{ ...server, readOnlyTrust: { ...server.readOnlyTrust!, trusted: true, added: ['new_tool'], changed: [{ name: 'lookup', parts: ['description', 'annotations'] }], removed: ['old_tool'] } }} />)
    expect(screen.getByText('lookup: description, annotations changed')).toBeInTheDocument()
    expect(screen.getByText('new_tool added')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Stop trusting labels' }))
    await waitFor(() => expect(api.revokeMcpReadOnlyTrust).toHaveBeenCalledWith('local-server'))
  })
})
