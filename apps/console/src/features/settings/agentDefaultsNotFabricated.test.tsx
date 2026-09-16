import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const SS_KEY = 'cache:settings:agent-defaults'
const read = (rel: string) => readFileSync(join(process.cwd(), "src", rel), 'utf8')
const strip = (s: string) => s
  .replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')

const cfgMock = vi.fn(async (): Promise<unknown> => ({ agent: { approval_mode: 'auto', yolo: true } }))

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  cfgMock.mockReset()
  cfgMock.mockResolvedValue({ agent: { approval_mode: 'auto', yolo: true } })
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      ...(await orig<{ api: Record<string, unknown> }>()).api,
      gideonConfig: cfgMock,
      agents: async () => ({ agents: [], default_agent: 'scout' }),
      agentProviders: async () => [],
      patchConfig: async () => ({ ok: true }),
      skills: async () => [],
      tools: async () => [],
      hooks: async () => [],
    },
  }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

async function mountPanel() {
  const { AgentDefaultsPanel } = await import('./AgentDefaultsPanel')
  return render(<AgentDefaultsPanel />)
}

describe('the panel refuses to present values it never loaded', () => {
  it('a failed config read replaces the form with the failure, not with defaults', async () => {
    cfgMock.mockRejectedValue(new Error('boom'))
    await mountPanel()
    await waitFor(() => expect(screen.getByText(/Couldn’t load|Couldn't load/)).toBeInTheDocument())
    expect(screen.queryByRole('switch'), 'no switch may be offered on an unloaded config').toBeNull()
  })

  it('🔴 THE MECHANISM: a resolved-but-EMPTY cache entry defeats that guard entirely', async () => {
    sessionStorage.setItem(SS_KEY, JSON.stringify({ v: { cfg: {}, defaultAgent: '' }, at: Date.now() }))
    cfgMock.mockRejectedValue(new Error('boom'))
    await mountPanel()
    await waitFor(() => expect(screen.queryAllByRole('switch').length).toBeGreaterThan(0))
    expect(screen.queryByText(/Couldn’t load|Couldn't load/),
      'the panel cannot tell a substituted success from a real one').toBeNull()
  })
})

describe('the hub can no longer write that entry', () => {
  const widgets = strip(read('features/settings/settingsWidgets.tsx'))

  it('its fetcher rejects on a failed config read, so nothing is cached', () => {
    const at = widgets.indexOf("'settings:agent-defaults'")
    expect(at, 'the hook must still exist').toBeGreaterThan(-1)
    const hook = widgets.slice(at, widgets.indexOf('persist: true', at) + 20)
    expect(hook, 'no substitute on the governing read')
      .not.toMatch(/gideonConfig\(\)[^\n]*\.catch\(/)
  })

  it('🪤 the DECORATING read keeps its fallback, matching the panel byte for byte', () => {
    const at = widgets.indexOf("'settings:agent-defaults'")
    const hook = widgets.slice(at, widgets.indexOf('persist: true', at) + 20)
    expect(hook).toMatch(/api\.agents\(\)\.then\(\(a\) => a\.default_agent\)\.catch\(\(\) => ''\)/)
    expect(read('features/settings/AgentDefaultsPanel.tsx'), 'and the panel spells the same fallback')
      .toMatch(/default_agent\)\.catch\(\(\) => ''\)/)
  })
})
