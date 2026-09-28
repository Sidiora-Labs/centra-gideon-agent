import { describe, expect, it } from 'vitest'
import { disabledMcpToolItems, mcpToggleTarget } from './ToolsPage'

describe('canonical MCP tool toggles', () => {
  it('maps the canonical identity back to the exact server and raw tool name', () => {
    expect(mcpToggleTarget('calendar', 'mcp/calendar/create_event')).toEqual({
      server: 'calendar',
      tool: 'create_event',
    })
  })

  it('rejects a tool owned by another server and a prefixed raw tool value', () => {
    expect(mcpToggleTarget('calendar', 'mcp/drive/read_file')).toBeNull()
    expect(mcpToggleTarget('calendar', 'mcp/calendar/mcp/calendar/create_event')).toBeNull()
  })

  it('keeps a disabled server tool visible with its canonical identity for re-enabling', () => {
    const item = disabledMcpToolItems({
      name: 'calendar',
      status: 'ready',
      tools: [{ name: 'create_event', description: 'Create an event' }],
      disabledTools: ['create_event'],
    }, [])
    expect(item).toEqual([expect.objectContaining({
      name: 'mcp/calendar/create_event',
      serverTool: 'mcp/calendar/create_event',
      description: 'Create an event',
      disabled: true,
    })])
  })
})
