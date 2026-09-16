import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const okExternal = {
  enabled: false, surfaces: [], clients: [], caps: {}, incident_active: false,
}
const boom = () => Promise.reject(new Error('gateway down'))

describe('ExternalAccessPanel never asserts an exposure state it did not read', () => {
  beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

  const mock = (over: Record<string, unknown>) => {
    vi.doMock('../../shared/data/api', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      api: { externalAccess: () => Promise.resolve(okExternal), patchConfig: () => Promise.resolve({}), ...over },
    }))
  }

  it('a failed read shows a retryable error and NO switch', async () => {
    mock({ externalAccess: boom })
    const { ExternalAccessPanel } = await import('./ExternalAccessPanel')
    render(<ExternalAccessPanel />)
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(/external access/i)
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()
    expect(screen.queryByRole('switch'), 'a failed read must not render a switch').toBeNull()
    expect(screen.queryByRole('checkbox'), 'nor a checkbox form of one').toBeNull()
  })

  it('a successful read DOES render switches — so the assertion above is not vacuous', async () => {
    mock({})
    const { ExternalAccessPanel } = await import('./ExternalAccessPanel')
    render(<ExternalAccessPanel />)
    await waitFor(() => {
      const controls = screen.queryAllByRole('switch').length + screen.queryAllByRole('checkbox').length
      expect(controls, 'a loaded panel renders its controls').toBeGreaterThan(0)
    })
    expect(screen.queryByRole('alert'), 'a successful read is not an error').toBeNull()
  })
})

describe('the fetchers propagate the read a claim is made about', () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  const src = (p: string) => strip(readFileSync(join(process.cwd(), "src/features/settings", p), 'utf8'))

  it('ExternalAccessPanel no longer eats its rejection, and branches on status', () => {
    const code = src('ExternalAccessPanel.tsx')
    expect(code, 'the swallow is what made the error branch unreachable')
      .not.toMatch(/api\.externalAccess\(\)\.catch/)
    expect(code, 'and the panel reads the status useQuery derives from it').toMatch(/status === 'error'/)
    expect(code, 'with a loading branch too — undefined data is not "off"').toMatch(/status === 'loading'/)
  })

  it('ProvidersPanel no longer eats the providers rejection', () => {
    const code = src('ProvidersPanel.tsx')
    expect(code, '`[]` is truthy, so this catch made the skeleton gate unreachable')
      .not.toMatch(/api\.settingsProviders\(\)\.catch/)
    expect(code, 'and the failure branch precedes the skeleton, which cannot tell the two apart')
      .toMatch(/providersStatus === 'error'/)
  })

  it('the ENRICHMENT reads stay tolerant — that split is the design, not an oversight', () => {
    const code = src('ProvidersPanel.tsx')
    for (const call of ['api.agentRuntimes()', 'api.modelsAvailable()', 'api.channels()']) {
      expect(code, `${call} keeps its catch: a dead subsystem renders as an unready card, not a dead page`)
        .toContain(`${call}.catch`)
    }
  })
})
