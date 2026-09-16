import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { TRUST_TIER_LABEL } from '../../shared/data/trustTier'


const LOCKED = 'gideon-filesystem'

const tool = (name: string, provider: string, tier: string | undefined) => ({
  name, provider, description: `${name} description`, parameters: {},
  requires_approval: false, risk_level: 'safe', ...(tier === undefined ? {} : { tier }),
})

const THREE_PROVIDERS = [
  tool('read_file', LOCKED, 'builtin'),
  tool('memory_search', 'gideon-memory', 'builtin'),
  tool('spec_outline', 'spec-builder', 'community'),
]

function mockApi(tools: unknown[]) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex: () => Promise.resolve({ tools, load_failures: [] }),
      mcpServers: () => Promise.resolve([]),
      importableMcp: () => Promise.resolve([]),
      mcpPoolStats: () => Promise.resolve({ available: false }),
      toolGroups: () => Promise.resolve(null),
    },
  }))
}

async function mount(tools: unknown[]) {
  mockApi(tools)
  const { ToolsPage } = await import('./ToolsPage')
  render(<ToolsPage query={{}} setQuery={() => {}} />)
  await waitFor(() => expect(screen.getByText(String((tools.at(-1) as { name: string }).name))).toBeInTheDocument())
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the Tools provenance badge has three states, derived from where the provider came from', () => {
  it('an installed community bundle reads its own tier and NEVER `built-in`', async () => {
    await mount(THREE_PROVIDERS)
    const community = TRUST_TIER_LABEL.community
    expect(screen.getAllByText(community).length, `the bundle is badged "${community}"`).toBeGreaterThan(0)
    const page = document.body.textContent || ''
    const builtInCount = page.split(TRUST_TIER_LABEL.builtin).length - 1
    expect(builtInCount, `"${TRUST_TIER_LABEL.builtin}" belongs to core alone`).toBe(1)
  })

  it('a genuine core provider still reads `built-in`', async () => {
    await mount(THREE_PROVIDERS)
    const badge = screen.getByText(TRUST_TIER_LABEL.builtin)
    expect(badge).toBeInTheDocument()
    const row = badge.parentElement
    expect(row?.textContent, 'the built-in badge sits beside the core provider').toContain('gideon-memory')
    expect(row?.textContent, 'not beside the installed bundle').not.toContain('spec-builder')
  })

  it('a provider-locked core provider still reads `platform`', async () => {
    await mount(THREE_PROVIDERS)
    const badge = screen.getByText('platform')
    expect(badge.parentElement?.textContent, 'the platform badge sits beside the locked provider').toContain(LOCKED)
  })

  it('an UNKNOWN tier renders no provenance badge at all — never `built-in`', async () => {
    await mount([tool('mystery_tool', 'unknown-provider', undefined)])
    const page = document.body.textContent || ''
    expect(page, 'the provider is still listed').toContain('unknown-provider')
    expect(page, 'but no tier is claimed for it').not.toContain(TRUST_TIER_LABEL.builtin)
    expect(page).not.toContain(TRUST_TIER_LABEL.community)
    expect(page).not.toContain('platform')
  })
})

describe('the Tools badge and the install dialog spell the same tier the same way', () => {
  it('both surfaces render the string the shared map defines for `community`', async () => {
    const expected = TRUST_TIER_LABEL.community

    await mount(THREE_PROVIDERS)
    const badgeText = screen.getAllByText(expected)
    expect(badgeText.length, 'the Tools badge').toBeGreaterThan(0)
    const toolsPageText = document.body.textContent || ''

    const { ScanReport } = await import('../apps/installConsent')
    const { container } = render(
      <ScanReport scan={{
        verdict: 'warning', tier: 'community', findings: [],
        signature: { state: 'unsigned', signer: '', reason: '' },
      } as never} />,
    )
    const dialogText = container.textContent || ''
    expect(dialogText, 'the install dialog').toContain(expected)
    expect(dialogText).toContain(`Unsigned — ${expected}`)
    expect(toolsPageText).toContain(expected)
  })
})

describe('neither surface keeps a tier literal of its own', () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  const read = (rel: string) => strip(readFileSync(join(process.cwd(), rel), 'utf8'))

  it('ToolsPage derives the badge from the shared map, not from a `built-in` literal', () => {
    const code = read('src/features/tools/ToolsPage.tsx')
    expect(code, 'no standalone built-in literal').not.toMatch(/(['"`])built-in\1/)
    expect(code, 'the badge reads the shared map').toMatch(/trustTierLabel\(/)
  })

  it('installConsent derives the signature row from the shared map, not from a `community tier` literal', () => {
    const code = read('src/features/apps/installConsent.tsx')
    expect(code, 'no hardcoded tier phrase').not.toMatch(/community tier/)
    expect(code, 'the row reads the shared map').toMatch(/trustTierLabel\(/)
  })
})
