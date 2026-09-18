import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'

/**
 * req 91 — the coral rail means "this agent is the globally active default", and built-in
 * identity keeps its own signals (icon + lock badge), rendered.
 *
 * `agentRailIsActive.test.ts` reads the SOURCE of `NativeRow` and matches the ternary. That
 * rail cannot see what the browser paints: a reformat, a wrapper component, or a `ListRow`
 * that stopped drawing `accent` would leave it green with no rail on screen. These mount the
 * real page against the real `ListRow` and assert on the DOM, with the built-in row and the
 * active row being DIFFERENT agents so "native" and "active" cannot be confused.
 */

const RAIL = 'var(--color-primary)'

const catalog = {
  agents: [
    { name: 'scout', model: 'x', reserved: true },
    { name: 'probe', model: 'y' },
  ],
  default_agent: 'probe',
}

function mockApi() {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      agents: () => Promise.resolve(catalog),
      agentProviders: () => Promise.resolve([]),
      syncAgents: () => Promise.resolve({ ok: true }),
      setDefaultAgent: vi.fn(),
    },
  }))
}

async function mountList() {
  const { AgentsListPage } = await import('./AgentsListPage')
  render(<AgentsListPage query={{}} setQuery={() => {}} onCreate={() => {}} />)
  await screen.findByRole('button', { name: 'probe' })
}

function rowFor(name: string): HTMLElement {
  const target = screen.getByRole('button', { name })
  const row = target.parentElement
  if (!row) throw new Error(`no row rendered for ${name}`)
  return row as HTMLElement
}

function railsIn(row: HTMLElement): HTMLElement[] {
  return [...row.querySelectorAll('span')].filter((el) => (el as HTMLElement).style.background === RAIL) as HTMLElement[]
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  mockApi()
})

afterEach(() => { vi.restoreAllMocks() })

describe('the rendered agent rail', () => {
  it('paints the coral rail on the globally active default', async () => {
    await mountList()
    expect(railsIn(rowFor('probe')), 'the active default carries no rail').toHaveLength(1)
  })

  it('leaves a built-in that is NOT the default without a rail', async () => {
    await mountList()
    expect(railsIn(rowFor('scout')), 'the rail is riding "native", not "active"').toHaveLength(0)
  })

  it('keeps the built-in row identifiable by its icon and badge', async () => {
    await mountList()
    const builtin = rowFor('scout')
    expect(builtin.textContent).toContain('built-in')
    expect(builtin.querySelector('.bg-primary\\/10'), 'the native icon chip is the group identity').not.toBeNull()
    expect(builtin.querySelector('svg'), 'the row still renders its icon').not.toBeNull()
  })

  it('marks the default row as default in words, not only in colour', async () => {
    await mountList()
    expect(rowFor('probe').textContent).toContain('default')
    expect(rowFor('scout').textContent).not.toContain('default')
  })
})
