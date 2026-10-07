import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const PAGES = join(process.cwd(), "src/features")
const SETTINGS = join(PAGES, 'settings/InboxSettingsPanel.tsx')
const DRAWER = join(PAGES, 'inbox/InboxSettingsPanel.tsx')

function patchedFlags(src: string): Set<string> {
  return new Set([...src.matchAll(/patchConfig\(\s*'([^']+)'/g)].map((m) => m[1]))
}
function savedFields(src: string): Set<string> {
  return new Set([...src.matchAll(/patch\(\{\s*([a-z_]+):/g)].map((m) => m[1]))
}

describe('the two InboxSettingsPanel implementations', () => {
  const settings = readFileSync(SETTINGS, 'utf8')
  const drawerPanel = readFileSync(DRAWER, 'utf8')
  const watched = readFileSync(join(PAGES, 'inbox/WatchedChannelsField.tsx'), 'utf8')
  const drawerState = readFileSync(join(PAGES, 'inbox/inboxSettingsState.ts'), 'utf8')
  const drawer = `${drawerPanel}\n${drawerState}\n${watched}`
  const settingsReach = `${settings}\n${watched}`

  it('both files exist and are non-trivial (guards a silently-empty scan)', () => {
    expect(settings.length).toBeGreaterThan(500)
    expect(drawer.length).toBeGreaterThan(500)
  })

  it('Settings → Inbox reaches every config flag the drawer does', () => {
    const missing = [...patchedFlags(drawer)].filter((f) => !patchedFlags(settingsReach).has(f))
    expect(
      missing,
      'These inbox config flags are editable ONLY from the in-context drawer, so a user who ' +
        'goes to Settings → Inbox — where every other inbox setting lives — cannot reach them:\n  ' +
        missing.join('\n  '),
    ).toEqual([])
  })

  it('Settings → Inbox reaches every stored field the drawer does', () => {
    const missing = [...savedFields(drawer)].filter((f) => !savedFields(settingsReach).has(f))
    expect(missing, `entity-settings fields missing from the canonical panel: ${missing}`).toEqual([])
  })

  it('both native panels render the same revision-aware watched-channel editor', () => {
    expect(settings).toContain("import { WatchedChannelsField } from '../inbox/WatchedChannelsField'")
    expect(drawerPanel).toContain("import { WatchedChannelsField } from './WatchedChannelsField'")
    expect(settings).toContain('<WatchedChannelsField />')
    expect(drawerPanel).toContain('<WatchedChannelsField />')
    expect([...patchedFlags(drawer)].sort()).toEqual(['inbox.enabled', 'inbox.engagement_ranking_enabled', 'inbox.watched_channels'])
    expect([...savedFields(drawer)].sort()).toEqual(['auto_cleanup_enabled', 'retention_days'])
    expect(watched).toContain("config.revisions?.['inbox.watched_channels']")
    expect(watched).toContain("typeof revision !== 'string' || !revision")
    expect(watched).toContain("write: (next, revision) => api.patchConfig('inbox.watched_channels', next, revision)")
    expect(watched).toContain('guard.apply(watchedDocument, operation)')
    expect(watched).toContain('watchedSources.some(source => source.watches_channels)')
    expect(watched).toContain('<StaleWriteNotice guard={guard}')
  })

  it('writes config flags through patchConfig, never the entity store', () => {
    for (const [name, src] of [['settings', settingsReach], ['drawer', drawer]] as const) {
      for (const flag of ['enabled', 'engagement_ranking_enabled']) {
        expect(
          savedFields(src).has(flag),
          `${name} panel writes ${flag} to the entity store; it belongs in patchConfig`,
        ).toBe(false)
      }
    }
  })
})
