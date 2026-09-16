import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const WEB = process.cwd()
const codeOf = (rel: string) =>
  readFileSync(join(WEB, rel), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')


function subpages(): { id: string; label: string }[] {
  const src = codeOf('src/features/settings/SettingsPage.tsx')
  const block = src.match(/const SUBPAGES: SubPage\[\] = \[([\s\S]*?)\n\]/)
  return [...(block?.[1] ?? '').matchAll(/^\s*\{ id: '([a-z-]+)', label: '([^']*)'/gm)]
    .map((m) => ({ id: m[1], label: m[2] }))
}

function widgets(): { id: string; group: string; label: string }[] {
  const src = codeOf('src/features/settings/settingsWidgets.tsx')
  const block = src.match(/export const SETTINGS_WIDGETS: SettingsWidget\[\] = \[([\s\S]*)/)
  return [...(block?.[1] ?? '').matchAll(/^\s*id: '([a-z-]+)', group: '([^']+)', label: '([^']*)'/gm)]
    .map((m) => ({ id: m[1], group: m[2], label: m[3] }))
}

const EXCLUDED = new Map<string, string>([
])

const GROUPS = new Set(['General', 'AI & Models', 'Workspace', 'System'])

describe('every settings subpage is reachable from the hub', () => {
  const subs = subpages()
  const hub = widgets()

  it('finds both populations, and the floor only rises (VACUITY)', () => {
    expect(subs.length, 'the SUBPAGES matcher must find the panels').toBeGreaterThanOrEqual(34)
    expect(hub.length, 'the SETTINGS_WIDGETS matcher must find the cards').toBeGreaterThanOrEqual(34)
    expect(new Set(hub.map((w) => w.id)).size, 'duplicate widget id').toBe(hub.length)
    expect(new Set(subs.map((s) => s.id)).size, 'duplicate subpage id').toBe(subs.length)
  })

  it('no subpage is reachable only by typing its URL', () => {
    const orphans = subs.filter((s) => !hub.some((w) => w.id === s.id) && !EXCLUDED.has(s.id))
    expect(
      orphans.map((s) => `${s.id}  (${s.label})`),
      'These panels ship, and NOTHING on the settings hub opens them — no card, and no entry in\n' +
        'the search index either, because the index is built from the widget. Add a widget to\n' +
        'SETTINGS_WIDGETS in web/src/pages/settings/settingsWidgets.tsx (borrow the title and the\n' +
        "one-line description from the panel's own PanelHeader), or exclude it with a reason:\n  " +
        orphans.map((s) => s.id).join('\n  '),
    ).toEqual([])
  })

  it('no card opens a subpage that does not exist', () => {
    const dangling = hub.filter((w) => !subs.some((s) => s.id === w.id))
    expect(
      dangling.map((w) => `${w.id}  (${w.label})`),
      'These hub cards navigate to a route with no panel — the click lands back on the hub:\n  ' +
        dangling.map((w) => w.id).join('\n  '),
    ).toEqual([])
  })

  it('the exclusion list is pinned, and holds nothing stale', () => {
    for (const [id, why] of EXCLUDED) {
      expect(subs.some((s) => s.id === id), `EXCLUDED holds '${id}', which is not a subpage`).toBe(true)
      expect(
        hub.some((w) => w.id === id),
        `'${id}' is excluded from the hub AND has a card — one of the two is wrong`,
      ).toBe(false)
      expect(why.length, `'${id}' needs a real reason, not a shrug`).toBeGreaterThan(30)
    }
    expect(EXCLUDED.size, 'an on-ramp to nothing is worse than none — but excluding a REAL panel hides it').toBe(0)
  })

  it('a card and its breadcrumb call the panel the same thing', () => {
    const drift = hub
      .filter((w) => subs.some((s) => s.id === w.id && s.label !== w.label))
      .map((w) => `${w.id}: card "${w.label}" vs breadcrumb "${subs.find((s) => s.id === w.id)!.label}"`)
    expect(drift, 'the card and the breadcrumb must agree').toEqual([])
  })

  it('every card sits in one of the hub\'s real groups', () => {
    const stray = hub.filter((w) => !GROUPS.has(w.group)).map((w) => `${w.id}: '${w.group}'`)
    expect(stray, 'an unrecognised group mints its own heading with one card under it').toEqual([])
    const used = new Set(hub.map((w) => w.group))
    expect([...GROUPS].filter((g) => !used.has(g)), 'GROUPS declares a heading no card uses').toEqual([])
  })

  it('every card opens its OWN subpage', () => {
    const src = codeOf('src/features/settings/settingsWidgets.tsx')
    const wrong: string[] = []
    for (const w of hub) {
      const at = src.indexOf(`id: '${w.id}', group: '${w.group}'`)
      const next = hub[hub.indexOf(w) + 1]
      const end = next ? src.indexOf(`id: '${next.id}', group: '${next.group}'`) : src.length
      const body = src.slice(at, end > at ? end : src.length)
      if (!body.includes(`go('${w.id}')`)) wrong.push(`${w.id}: no go('${w.id}') in its own render`)
    }
    expect(wrong, 'a card must navigate to the subpage it names').toEqual([])
  })
})
