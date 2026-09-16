import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

function sourceFiles(): string[] {
  const out: string[] = []
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name)
      if (e.isDirectory()) { walk(p); continue }
      if (!/\.tsx$/.test(e.name) || /\.test\.tsx$/.test(e.name)) continue
      if (p.endsWith(join('shared/ui', 'forms.tsx'))) continue
      out.push(p)
    }
  }
  walk(SRC)
  return out
}

const STEPPER_CHROME = /h-9[^"']*w-28[^"']*bg-surface-container|w-28[^"']*h-9[^"']*bg-surface-container/

describe('the canonical numeric stepper', () => {
  const files = sourceFiles()

  it('scans a real tree (guards against a silently-empty sweep)', () => {
    expect(files.length).toBeGreaterThan(50)
    expect(files.some((f) => f.endsWith(join('settings', 'MemoryPanel.tsx')))).toBe(true)
  })

  it('has no hand-rolled twin wearing the settings-stepper chrome', () => {
    const offenders: string[] = []
    for (const f of files) {
      const src = readFileSync(f, 'utf8')
      if (!/type="number"/.test(src)) continue
      for (const [i, line] of src.split('\n').entries()) {
        if (/type="number"/.test(line) || STEPPER_CHROME.test(line)) {
          const window = src.split('\n').slice(Math.max(0, i - 2), i + 3).join(' ')
          if (/type="number"/.test(window) && STEPPER_CHROME.test(window)) {
            offenders.push(`${f.slice(SRC.length + 1)}:${i + 1}`)
            break
          }
        }
      }
    }
    expect(
      offenders,
      'A clamped numeric setting must use NumberField from ui/forms — hand-rolling it loses ' +
        'clamp-on-commit, so every keystroke persists a garbage intermediate and an empty ' +
        'field commits 0:\n  ' + offenders.join('\n  '),
    ).toEqual([])
  })

  it('the two migrated panels reach for the primitive', () => {
    for (const rel of [join('settings', 'MemoryPanel.tsx'), join('inbox', 'InboxSettingsPanel.tsx')]) {
      const src = readFileSync(join(SRC, "features", rel), 'utf8')
      expect(src, `${rel} should render NumberField`).toMatch(/<NumberField\b/)
      expect(src, `${rel} still defines a local NumInput`).not.toMatch(/function NumInput\b/)
    }
  })
})
