import { describe, it, expect, vi } from 'vitest'
import { sourceGroup, localSourceLabel, type StoreItem } from './AppsSection'


vi.mock('../../shared/data/api', () => ({ api: {} }))
vi.mock('../../shared/data/useGuardedInstall', () => ({
  useGuardedInstall: () => ({}),
  guardedFromApp: (r: unknown) => r,
  isBlockingResult: () => false,
  terminalRefusalReason: () => null,
}))
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

const base: StoreItem = {
  name: 'ledger', displayName: 'Ledger', description: '', version: '1.0.0',
  icon: '', author: '', source: '', sourceKind: 'local',
  isProvider: false, providerType: '', tags: [],
  installed: true, enabled: true, hasUI: true,
}
const mk = (over: Partial<StoreItem>): StoreItem => ({ ...base, ...over })

describe('localSourceLabel turns a filesystem path into a human folder name', () => {
  it('uses the last path segment, not the whole absolute path', () => {
    expect(localSourceLabel('/Users/me/projects/cool-app')).toBe('cool-app')
    expect(localSourceLabel('/srv/apps')).toBe('apps')
  })
  it('ignores a trailing slash', () => {
    expect(localSourceLabel('/Users/me/projects/cool-app/')).toBe('cool-app')
  })
  it('handles Windows separators', () => {
    expect(localSourceLabel('C:\\Users\\me\\apps')).toBe('apps')
  })
  it('never returns a value containing a path separator for a real folder', () => {
    const leaky = '/home/operator/projects/gideon/.worktrees/ux-inspect/runtime/gideon/extensions/apps'
    expect(localSourceLabel(leaky)).toBe('apps')
    expect(localSourceLabel(leaky)).not.toContain('/')
  })
  it('falls back to the input when there is no segment to show', () => {
    expect(localSourceLabel('/')).toBe('/')
  })
})

describe('sourceGroup labels a local source by folder name, keyed by full path', () => {
  it('a worktree-checkout source does not leak the absolute path into the heading (WT-10)', () => {
    const worktree = '/home/operator/projects/gideon/.worktrees/ux-inspect/runtime/gideon/extensions/apps'
    const g = sourceGroup(mk({ source: `${worktree}/ledger`, sourceKind: 'local', origin: 'local' }), [])
    expect(g.label).toBe('apps')
    expect(g.label).not.toContain('/')
    expect(g.label.startsWith('/Users')).toBe(false)
    expect(g.key).toBe(`local:${worktree}`)
  })

  it('folds an app up to its registered local source and labels it by that folder', () => {
    const g = sourceGroup(mk({ source: '/srv/apps/ledger', sourceKind: 'local', origin: 'local' }), ['/srv/apps'])
    expect(g.key).toBe('local:/srv/apps')
    expect(g.label).toBe('apps')
  })

  it('still shows a git URL verbatim as its own heading', () => {
    const url = 'https://github.com/acme/cool-app.git'
    const g = sourceGroup(mk({ source: url, sourceKind: 'git', origin: 'external', installed: true }), [])
    expect(g.key).toBe(`git:${url}`)
    expect(g.label).toBe(url)
  })

  it('still folds bundled/native apps into one "Built-in" group', () => {
    expect(sourceGroup(mk({ native: true, source: '/anywhere/on/disk' }), []).label).toBe('Built-in')
    expect(sourceGroup(mk({ sourceKind: 'bundled', installed: false, source: '' }), []).label).toBe('Built-in')
  })
})
