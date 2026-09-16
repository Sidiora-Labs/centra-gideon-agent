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
  const drawer = readFileSync(DRAWER, 'utf8')

  it('both files exist and are non-trivial (guards a silently-empty scan)', () => {
    expect(settings.length).toBeGreaterThan(500)
    expect(drawer.length).toBeGreaterThan(500)
  })

  it('Settings → Inbox reaches every config flag the drawer does', () => {
    const missing = [...patchedFlags(drawer)].filter((f) => !patchedFlags(settings).has(f))
    expect(
      missing,
      'These inbox config flags are editable ONLY from the in-context drawer, so a user who ' +
        'goes to Settings → Inbox — where every other inbox setting lives — cannot reach them:\n  ' +
        missing.join('\n  '),
    ).toEqual([])
  })

  it('Settings → Inbox reaches every stored field the drawer does', () => {
    const missing = [...savedFields(drawer)].filter((f) => !savedFields(settings).has(f))
    expect(missing, `entity-settings fields missing from the canonical panel: ${missing}`).toEqual([])
  })

  it('writes config flags through patchConfig, never the entity store', () => {
    for (const [name, src] of [['settings', settings], ['drawer', drawer]] as const) {
      for (const flag of ['enabled', 'engagement_ranking_enabled']) {
        expect(
          savedFields(src).has(flag),
          `${name} panel writes ${flag} to the entity store; it belongs in patchConfig`,
        ).toBe(false)
      }
    }
  })
})
