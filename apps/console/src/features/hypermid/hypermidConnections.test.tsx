import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { HypermidIntegrationsWire } from '../../shared/data/api'
import { ConnectionsView } from './Connections'

const snapshot: HypermidIntegrationsWire = {
  scope: { owner_id: 'owner', project_id: 'project' },
  checked_at_ms: Date.parse('2026-10-02T12:00:00Z'),
  connections: [
    {
      connection_id: 'hypermid-host', kind: 'host', coverage: 'full_host', transport: 'daemon',
      state: 'unavailable', available: false, display_name: 'Hypermid host', capabilities: [], operations: [],
      module_id: null, connected_sessions: null, catalog_generation: null, active_calls: null, process_ready: null,
      budget: { state: 'unavailable' }, fault: { state: 'unavailable', code: 'host_lifecycle_unavailable' },
    },
    {
      connection_id: 'mcp-bridge', kind: 'mcp_bridge', coverage: 'tool_bridge', transport: 'stdio',
      state: 'ready', available: true, display_name: 'Daemon MCP bridge', capabilities: ['catalog', 'invoke'], operations: ['stop'],
      module_id: null, connected_sessions: 2, catalog_generation: 4, active_calls: 1, process_ready: true,
      budget: { state: 'unavailable' }, fault: { state: 'unavailable', code: null },
    },
  ],
  conditions: [{
    condition_id: null, availability: 'unavailable', result: null, transition_id: null,
    transition_identity: 'unavailable', failure_code: 'condition_source_unavailable', checked_at_ms: Date.parse('2026-10-02T12:00:00Z'), observed_facts: [],
  }, {
    condition_id: 'repository-ready', availability: 'available', result: true, transition_id: 'transition-4',
    transition_identity: 'available', failure_code: null, checked_at_ms: Date.parse('2026-10-02T12:01:00Z'),
    observed_facts: [{ label: 'Source', value: 'Local Git repository' }, { label: 'Match', value: 'Present' }],
  }],
}

describe('Hypermid Connections', () => {
  it('keeps full host coverage separate from tool availability and exposes missing native condition authority', () => {
    const html = renderToStaticMarkup(<ConnectionsView snapshot={snapshot} onRefresh={vi.fn()} onAction={vi.fn()} />)
    expect(html).toContain('Full host integration')
    expect(html).toContain('Tool bridge')
    expect(html.indexOf('Hypermid host')).toBeLessThan(html.indexOf('Daemon MCP bridge'))
    expect(html).toContain('Tool bridges expose only their listed catalog and call capabilities')
    expect(html).toContain('Condition source unavailable')
    expect(html).toContain('Local Git repository')
    expect(html).toContain('Transition transition-4')
    expect(html).toContain('connected sessions')
    expect(html).not.toContain('Full Gideon integration')
  })
})
