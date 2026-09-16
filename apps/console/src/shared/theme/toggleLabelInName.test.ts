import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.tsx$/.test(name) && !name.includes('.test.')) out.push(abs)
  }
  return out
}

const spoken = (s: string) => s.toLowerCase().replace(/[‘’“”'"`]/g, '').replace(/[-–—]/g, ' ').replace(/\s+/g, ' ').trim()

type Pair = { file: string; visible: string; name: string }

function pairs(): Pair[] {
  const found: Pair[] = []
  for (const abs of walk(SRC)) {
    const code = readFileSync(abs, 'utf8').replace(/=>/g, '⇒')
    const re = /<(?:Row|Field)\s+label="([^"]+)"[^>]*>\s*(?:\{[^}]*\}\s*)?(?:<div[^>]*>\s*)?(?:<[A-Z][^>]*\/>\s*)*<Toggle\b[^>]*?label="([^"]+)"/gs
    for (const m of code.matchAll(re)) found.push({ file: abs.replace(SRC + '/', ''), visible: m[1], name: m[2] })
  }
  return found
}

describe('a toggle’s accessible name contains its visible row label', () => {
  const all = pairs()

  it('the sweep sees a real population — the vacuity floor', () => {
    expect(all.length, 'Row/Field-wrapped labelled toggles found').toBeGreaterThanOrEqual(25)
  })

  it('no switch name truncates the label the user can see', () => {
    const offenders = all
      .filter((p) => !spoken(p.name).includes(spoken(p.visible)))
      .map((p) => `${p.file}: visible ${JSON.stringify(p.visible)} → name ${JSON.stringify(p.name)}`)
    expect(offenders, 'WCAG 2.5.3 Label in Name').toEqual([])
  })

  it('the five sites use the row’s own words', () => {
    const notif = readFileSync(join(SRC, 'features/settings/NotificationsPanel.tsx'), 'utf8')
    const chat = readFileSync(join(SRC, 'features/settings/ChatPanel.tsx'), 'utf8')
    expect(notif).toMatch(/label="Mute all notifications" \/>/)
    expect(notif).toMatch(/label="Enable quiet hours" \/>/)
    expect(chat).toMatch(/label="Restore sessions on startup" \/>/)
    expect(readFileSync(join(SRC, 'features/settings/NotificationRulesMatrix.tsx'), 'utf8'))
      .toMatch(/label="Escalate on name mention" \/>/)
  })

  it('the visible rows are untouched — this changed a NAME, not any copy', () => {
    const notif = readFileSync(join(SRC, 'features/settings/NotificationsPanel.tsx'), 'utf8')
    const chat = readFileSync(join(SRC, 'features/settings/ChatPanel.tsx'), 'utf8')
    expect(notif).toMatch(/<Row label="Mute all notifications" hint="Pause every notification regardless of severity\.">/)
    expect(notif).toMatch(/<Row label="Enable quiet hours">/)
    expect(chat).toMatch(/<Row label="Restore sessions on startup"/)
  })

  it('the quote-glyph pair is deliberately left alone', () => {
    const chat = readFileSync(join(SRC, 'features/settings/ChatPanel.tsx'), 'utf8')
    expect(chat).toMatch(/label="Offer 'Check this work'"/)
  })

  it('Toggle still puts its label on the switch as aria-label — the mechanism this rests on', () => {
    expect(readFileSync(join(SRC, 'shared/ui/Toggle.tsx'), 'utf8')).toMatch(/role="switch"[^>]*aria-label=\{label\}/)
  })
})
