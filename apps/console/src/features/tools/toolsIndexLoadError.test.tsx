import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const boom = () => Promise.reject(new Error('gateway down'))
const idx = { tools: [{ name: 'read_file', provider: 'native', risk: 'low' }], load_failures: [] }

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex: () => Promise.resolve(idx),
      mcpServers: () => Promise.resolve([]),
      importableMcp: () => Promise.resolve([]),
      mcpPoolStats: () => Promise.resolve({ available: false }),
      toolGroups: () => Promise.resolve(null),
      ...over,
    },
  }))
}

async function mount() {
  const { ToolsPage } = await import('./ToolsPage')
  render(<ToolsPage query={{}} setQuery={() => {}} />)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('#/tools distinguishes a failed index read from an empty toolbox', () => {
  it('shows a retryable LoadError when the index read rejects', async () => {
    mockApi({ toolsIndex: boom })
    await mount()
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(/tools/i)
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No tools' }), 'not the newcomer empty state').toBeNull()
  })

  it('still shows "No tools" when the toolbox really is empty', async () => {
    mockApi({ toolsIndex: () => Promise.resolve({ tools: [], load_failures: [] }) })
    await mount()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No tools' })).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'an empty toolbox is not an error').toBeNull()
  })

  it('a failed MCP-server read still renders the tools — partial tolerance is the design', async () => {
    mockApi({ mcpServers: boom, importableMcp: boom, mcpPoolStats: boom, toolGroups: boom })
    await mount()
    await waitFor(() => expect(screen.getByText('read_file')).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'a peripheral read is not a page failure').toBeNull()
  })
})

describe('the composed fetcher keeps its asymmetry legible', () => {
  const code = readFileSync(join(process.cwd(), "src/features/tools/ToolsPage.tsx"), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  const readList = () => {
    const at = code.indexOf('await Promise.all([')
    expect(at, 'the composed fetcher must still be here').toBeGreaterThan(-1)
    let i = code.indexOf('[', at) + 1
    let depth = 1
    while (i < code.length && depth > 0) {
      if (code[i] === '[') depth++
      else if (code[i] === ']') depth--
      i++
    }
    return code.slice(at, i)
  }

  it('the index read carries no fallback of its own', () => {
    const list = readList()
    expect(list, 'the index must be in the list').toContain('api.toolsIndex()')
    expect(list, 'the index must not substitute an empty collection')
      .not.toMatch(/api\.toolsIndex\(\)\s*\.catch/)
  })

  it('the other four reads still DO carry one', () => {
    const list = readList()
    for (const read of ['api.mcpServers()', 'api.importableMcp()', 'api.mcpPoolStats()', 'api.toolGroups()']) {
      const at = list.indexOf(read)
      expect(at, `${read} must be in the list`).toBeGreaterThan(-1)
      expect(list.slice(at, at + 90), `${read} is peripheral and stays tolerant`).toMatch(/\.catch\(/)
    }
  })

  it('the error gate keeps a warm cache visible', () => {
    expect(code).toMatch(/tools === null && loadErr/)
    expect(code, 'and `loadErr` alone must not blank a populated page').not.toMatch(/\{loadErr \?\s*\(?\s*<LoadError/)
  })
})
