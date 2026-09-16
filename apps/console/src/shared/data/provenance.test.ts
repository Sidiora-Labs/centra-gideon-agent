import { describe, it, expect } from 'vitest'
import { provenance } from './provenance'
import { catalogApps } from './appCatalog'
import type { AppCatalogEntry } from './api'


describe('provenance() reads a sourceKind into words', () => {
  it('says built-in for the two shipped kinds', () => {
    expect(provenance({ sourceKind: 'native' })?.label).toBe('built-in')
    expect(provenance({ sourceKind: 'bundled' })?.label).toBe('built-in')
  })

  it('tells a first-party dir, a user dir and a remote apart', () => {
    expect(provenance({ sourceKind: 'first-party' })?.label).toBe('first-party')
    expect(provenance({ sourceKind: 'local' })?.label).toBe('local')
    expect(provenance({ sourceKind: 'git' })?.label).toBe('git')
  })

  it('the locked platform provider is its own word, not "built-in"', () => {
    expect(provenance({ locked: true })?.label).toBe('platform')
    expect(provenance({ sourceKind: 'local', locked: true })?.label).toBe('platform')
  })

  it('returns null — not a guess — for an origin it does not know', () => {
    expect(provenance({})).toBeNull()
    expect(provenance({ sourceKind: '' })).toBeNull()
    expect(provenance({ sourceKind: null })).toBeNull()
    expect(provenance({ sourceKind: 'something-new' })).toBeNull()
  })

  it('every label carries a title that explains what the word means', () => {
    for (const kind of ['native', 'bundled', 'first-party', 'local', 'git']) {
      const p = provenance({ sourceKind: kind })
      expect(p, kind).not.toBeNull()
      expect(p!.title.length, kind).toBeGreaterThan(20)
    }
    expect(provenance({ locked: true })!.title.length).toBeGreaterThan(20)
  })

  it('a remote and a local copy of one name are never given the same word', () => {
    expect(provenance({ sourceKind: 'git' })!.label)
      .not.toBe(provenance({ sourceKind: 'local' })!.label)
  })
})


const app = (name: string, over: Partial<AppCatalogEntry> = {}): AppCatalogEntry => ({
  name, displayName: name, description: '', version: '1.0.0', icon: '', author: '',
  source: `/srv/${name}`, sourceKind: 'local', isProvider: false, providerType: '', tags: [],
  ...over,
})

describe('catalogApps() flattens the four lists once, in the backend order', () => {
  it('keeps every app across all four lists', () => {
    const names = catalogApps({
      bundled: [app('a')], localApps: [app('b')], remoteApps: [app('c')], gitApps: [app('d')],
    }).map((e) => e.name)
    expect(names).toEqual(['a', 'b', 'c', 'd'])
  })

  it('tolerates a null catalog and missing lists', () => {
    expect(catalogApps(null)).toEqual([])
    expect(catalogApps(undefined)).toEqual([])
    expect(catalogApps({ bundled: [app('a')] }).map((e) => e.name)).toEqual(['a'])
  })

  it('if a name ever reaches two lists again, every consumer at least agrees which one wins', () => {
    const merged = catalogApps({
      localApps: [app('deep-research', { description: 'on disk', sourceKind: 'local' })],
      gitApps: [app('deep-research', { description: 'remote', sourceKind: 'git' })],
    })
    expect(merged).toHaveLength(1)
    expect(merged[0].description).toBe('on disk')
    expect(merged[0].sourceKind).toBe('local')
  })
})
