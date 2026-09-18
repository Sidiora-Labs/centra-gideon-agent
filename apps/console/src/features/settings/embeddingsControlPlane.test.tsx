import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { MODELS_PATH, MODELS_ROUTE } from '../chat/NoModelSetupState'

vi.mock('./ModelsPanel', () => ({ ModelsPanel: () => <div data-testid="models-panel">Models</div> }))

const { SettingsPage } = await import('./SettingsPage')

const WEB = process.cwd()
const SRC = join(WEB, 'src')

const REMOVED_ENDPOINTS = [
  '/api/memory/embedding-status',
  '/api/memory/embedding-models',
  '/api/memory/enable-embeddings',
  '/api/memory/disable-embeddings',
  '/api/memory/activate-model',
  '/api/memory/delete-model',
]

function sources(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) { out.push(...sources(full)); continue }
    if (/\.tsx?$/.test(name)) out.push(full)
  }
  return out
}

const SELF = join(SRC, 'features/settings/embeddingsControlPlane.test.tsx')
const FILES = sources(SRC).filter((f) => f !== SELF)
const codeOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the duplicate memory-level embedding controls are gone from the console', () => {
  it('scans the real tree (not an empty one)', () => {
    expect(FILES.length).toBeGreaterThan(200)
  })

  it('calls none of the removed memory-level embedding endpoints', () => {
    const offenders = FILES.filter((f) => {
      const code = readFileSync(f, 'utf8')
      return REMOVED_ENDPOINTS.some((path) => code.includes(path))
    }).map((f) => f.slice(SRC.length + 1))
    expect(
      offenders,
      'These files drive a second, memory-level embedding control plane. Settings →\n' +
        'Models owns model management; the memory endpoints are gone from the runtime,\n' +
        'so these calls would 404:\n  ' + offenders.join('\n  '),
    ).toEqual([])
  })

  it('still drives the ONE flow it kept, so the scan above is not vacuous', () => {
    const client = codeOf('shared/data/api.ts')
    expect(client).toContain('/api/models/embedding/reindex')
    expect(client).toContain('/api/models/active/')
  })

  it('leaves the Memory panel with no embedding model controls of its own', () => {
    const memory = codeOf('features/settings/MemoryPanel.tsx')
    expect(memory).not.toContain('ModelsPanel')
    for (const path of REMOVED_ENDPOINTS) expect(memory).not.toContain(path)
  })
})

describe('one reachable model-management flow', () => {
  it('mounts the Models panel from exactly one settings subpage', () => {
    const page = codeOf('features/settings/SettingsPage.tsx')
    expect(page.match(/<ModelsPanel\b/g) ?? []).toHaveLength(1)
    const ids = [...page.matchAll(/\{ id: '([a-z-]+)', label: '([^']+)'/g)]
    expect(ids.filter(([, , label]) => label === 'Models')).toHaveLength(1)
    expect(ids.find(([, , label]) => label === 'Models')?.[1]).toBe('models')
  })

  it('renders that panel for the settings/models route', () => {
    render(
      <SettingsPage sub="models" navigate={vi.fn()} navEpoch={0} query={{}} setQuery={vi.fn()} />,
    )
    expect(screen.getByTestId('models-panel')).toBeTruthy()
  })

  it('turns a settings-home tile id into that same route', () => {
    const page = codeOf('features/settings/SettingsPage.tsx')
    expect(page).toContain('navigate?.(id ? `settings/${id}` : \'settings\')')
    const widgets = codeOf('features/settings/settingsWidgets.tsx')
    const tiles = [...widgets.matchAll(/id: '([a-z-]+)', group: '[^']*', label: 'Models'/g)]
    expect(tiles).toHaveLength(1)
    expect(tiles[0][1]).toBe('models')
  })

  it('sends every other entry point to the same route', () => {
    expect(MODELS_PATH).toBe('settings/models')
    expect(MODELS_ROUTE).toBe(`#/${MODELS_PATH}`)
    for (const rel of [
      'shared/ui/DegradedChip.tsx',
      'features/learning/RetrievalBenchPanel.tsx',
      'features/learning/JudgeBenchPanel.tsx',
    ]) {
      expect(codeOf(rel), `${rel} must deep-link the one Models flow`).toContain(MODELS_ROUTE)
    }
  })

  it('leaves onboarding with no model-management surface of its own', () => {
    for (const f of sources(join(SRC, 'features/onboarding'))) {
      const code = readFileSync(f, 'utf8')
      for (const path of REMOVED_ENDPOINTS) expect(code).not.toContain(path)
      expect(code).not.toContain('ModelsPanel')
    }
  })
})
