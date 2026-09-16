import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const nativeOk = { agents: [{ name: 'scout', model: 'x' }], default_agent: 'scout' }
const boom = () => Promise.reject(new Error('gateway down'))

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      agents: () => Promise.resolve(nativeOk),
      agentProviders: () => Promise.resolve([]),
      syncAgents: () => Promise.resolve({ ok: true }),
      ...over,
    },
  }))
}

async function mount() {
  const { AgentsListPage } = await import('./AgentsListPage')
  render(<AgentsListPage query={{}} setQuery={() => {}} onCreate={() => {}} />)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('#/agents distinguishes a failed read from an empty catalog', () => {
  it('shows a retryable LoadError when the native read rejects', async () => {
    mockApi({ agents: boom })
    await mount()
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(/agents/i)
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No native agents' }), 'not the newcomer state').toBeNull()
  })

  it('still shows "No native agents" when the catalog really is empty', async () => {
    mockApi({ agents: () => Promise.resolve({ agents: [], default_agent: '' }) })
    await mount()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No native agents' })).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'an empty catalog is not an error').toBeNull()
  })

  it('a failed PROVIDER read still renders the native list — partial tolerance is the design', async () => {
    mockApi({ agentProviders: boom })
    await mount()
    await waitFor(() => expect(screen.getByText('scout')).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'a provider outage is not a page failure').toBeNull()
  })
})

describe('the adapter no longer swallows, and neither does its fetcher', () => {
  const src = readFileSync(join(process.cwd(), "src/features/agents/agentsData.ts"), 'utf8')
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('re-exposes the read error, matching the one adapter that always did', () => {
    expect(code, 'the hook must return the error it reads').toMatch(/return \{[\s\S]*?\berror\b/)
    expect(code, "and a `loaded` flag — `groups: []` cannot say 'not read yet'").toMatch(/loaded: data !== undefined/)
  })

  it('the native slice rejects rather than degrading to an empty group', () => {
    expect(code, 'Promise.all propagates the unhandled native rejection')
      .toMatch(/Promise\.all\(\[api\.agents\(\), api\.agentProviders\(\)\.catch\(\(\) => \[\]\)\]\)/)
    expect(code, 'the native request has no empty-catalog recovery').not.toMatch(/api\.agents\(\)\.catch/)
    expect(code, 'the native group uses the successfully loaded catalog').toMatch(/agents: native\.agents/)
  })

  it('the provider slice is still tolerant', () => {
    expect(code, 'provider discovery has an independent empty-list recovery')
      .toMatch(/api\.agentProviders\(\)\.catch\(\(\) => \[\]\)/)
  })
})
