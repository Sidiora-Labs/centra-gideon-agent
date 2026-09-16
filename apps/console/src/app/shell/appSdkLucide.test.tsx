
import { describe, it, expect, beforeAll } from 'vitest'
import { installAppSdk, resolvableAppSpecs } from './appSdk'

type ModuleMap = Record<string, unknown>
const mapOf = (): ModuleMap =>
  (window as unknown as { __gideon_modules: ModuleMap }).__gideon_modules

beforeAll(() => {
  installAppSdk()
})

describe('promise/map parity — every resolvable spec resolves', () => {
  it('every spec in the FULL promise (all capabilities) has a module map entry', () => {
    const specs = resolvableAppSpecs({ uiCapabilities: ['shell-primitives', 'generative-widget'] })
    const map = mapOf()
    const broken = specs.filter((s) => !(s in map))
    expect(broken, `promised to apps but absent from the module map (mount-breaking): ${broken.join(', ')}`).toEqual([])
  })

  it('the ungated head resolves for an app declaring NOTHING', () => {
    const specs = resolvableAppSpecs(undefined)
    const map = mapOf()
    for (const s of specs) expect(map[s], `${s} must resolve for every app`).toBeTruthy()
  })

  it('keeps one registry and stable SDK objects across repeated installation', () => {
    const registry = mapOf()
    const sdk = registry['@gideon/app-sdk']
    installAppSdk()
    expect(mapOf()).toBe(registry)
    expect(mapOf()['@gideon/app-sdk']).toBe(sdk)
    expect(Object.keys(registry).filter((name) => name.startsWith('@'))).toEqual([
      '@gideon/app-sdk', '@gideon/app-sdk/ui', '@gideon/app-sdk/genui',
    ])
  })
})

describe('the lucide-react vend', () => {
  it('serves the icons the shipped apps mark with emoji today', () => {
    const lucide = mapOf()['lucide-react'] as Record<string, unknown>
    for (const name of [
      'MessageSquare', 'FolderKanban', 'BookOpen', 'ListChecks', 'Link2', 'GitBranch',
      'Target', 'Sparkles', 'Check', 'Mic', 'Video', 'NotebookPen', 'FileText',
      'Presentation', 'Calendar', 'Users', 'SquareCheck',
    ]) {
      expect(lucide[name], `lucide vend is missing ${name}`).toBeTypeOf('object')
    }
  })

  it('is a curated vocabulary, not the whole namespace', () => {
    const lucide = mapOf()['lucide-react'] as Record<string, unknown>
    const count = Object.keys(lucide).length
    expect(count).toBeGreaterThanOrEqual(40)
    expect(count).toBeLessThan(120)
  })

  it('vends the HOST identities — the same component the host bundle renders', async () => {
    const lucide = mapOf()['lucide-react'] as Record<string, unknown>
    const host = await import('lucide-react')
    expect(lucide.MessageSquare).toBe(host.MessageSquare)
    expect(lucide.Loader2).toBe(host.Loader2)
  })
})

describe('the promise itself still lists lucide (the head this rail is about)', () => {
  it('resolvableAppSpecs keeps lucide-react in the ungated head', () => {
    expect(resolvableAppSpecs(undefined)).toContain('lucide-react')
  })


})
