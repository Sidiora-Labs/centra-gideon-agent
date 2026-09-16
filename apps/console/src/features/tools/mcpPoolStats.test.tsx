import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { McpPoolTile } from './ToolsPage'
import type { McpPoolStats } from '../../shared/data/api'


const base: McpPoolStats = {
  available: true,
  live_connections: 2,
  shared_conns: 1,
  session_conns: 1,
  configured_servers: 4,
  spawns: 3,
  reaps: 1,
  served: 9,
  evicted: 0,
  reused: 6,
}

const labels = (c: HTMLElement) =>
  [...c.querySelectorAll('.rounded-lg')].map((d) => d.textContent?.replace(/^\d+/, '').trim())

describe('configured_servers is shown and gates the tile', () => {
  it('renders the configured count', () => {
    const { container } = render(<McpPoolTile stats={base} />)
    expect(container.textContent).toContain('Configured')
    expect(labels(container)[0]).toBe('Configured')
  })

  it('renders for a pool with servers configured but nothing spawned yet', () => {
    const { container } = render(
      <McpPoolTile stats={{ ...base, live_connections: 0, shared_conns: 0, session_conns: 0, spawns: 0, reaps: 0, served: 0, reused: 0 }} />)
    expect(container.textContent).toContain('MCP connection pool')
    expect(container.textContent).toContain('Configured')
  })

  it('still renders nothing for a genuinely empty pool', () => {
    const { container } = render(
      <McpPoolTile stats={{ ...base, live_connections: 0, shared_conns: 0, session_conns: 0, configured_servers: 0, spawns: 0, reaps: 0, served: 0, reused: 0 }} />)
    expect(container.textContent).toBe('')
  })

  it('renders nothing when the pool is unavailable, whatever the counters say', () => {
    const { container } = render(<McpPoolTile stats={{ ...base, available: false }} />)
    expect(container.textContent).toBe('')
  })

  it('renders nothing for a null stats object', () => {
    expect(render(<McpPoolTile stats={null} />).container.textContent).toBe('')
  })
})

describe('evicted appears only when a session eviction has happened', () => {
  it('shows the count when non-zero', () => {
    const { container } = render(<McpPoolTile stats={{ ...base, evicted: 3 }} />)
    expect(container.textContent).toContain('Evicted')
    expect(labels(container)).toContain('Evicted')
  })

  it('is absent at zero', () => {
    expect(render(<McpPoolTile stats={base} />).container.textContent).not.toContain('Evicted')
  })
})

describe('served stays unrendered — it is derivable, not dropped', () => {
  it('the tile shows Reused rather than the raw served total', () => {
    const { container } = render(<McpPoolTile stats={base} />)
    expect(container.textContent).toContain('Reused')
    expect(labels(container)).not.toContain('Served')
  })
})
