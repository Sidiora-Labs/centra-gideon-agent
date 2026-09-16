import { describe, it, expect } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { FieldError } from '../../shared/ui/forms'

const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const strip = (s: string) => s
  .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/^(\s*)\/\/.*$/gm, '$1')
const srcOf = (rel: string) => strip(read(rel))
const APPS = 'features/apps/AppsSection.tsx'
const PANEL = 'features/settings/AppsPanel.tsx'

describe('the token the nine sites named does not exist, and the one they use now does', () => {
  const tokens = read('shared/theme/tokens.css')
  const light = tokens.slice(tokens.indexOf('.light'))

  it('🪤 --color-negative and --color-positive are defined NOWHERE', () => {
    expect(tokens, 'no --color-negative declaration').not.toMatch(/--color-negative\s*:/)
    expect(tokens, 'no --color-positive declaration').not.toMatch(/--color-positive\s*:/)
  })

  it('--color-danger and --color-ok are defined, in BOTH themes', () => {
    for (const t of ['--color-danger', '--color-ok']) {
      expect(tokens, `${t} is declared`).toMatch(new RegExp(`${t}\\s*:`))
      expect(light, `${t} is declared for light too`).toMatch(new RegExp(`${t}\\s*:`))
    }
  })

  it('no `negative`/`positive` colour reference survives anywhere in the app', () => {
    for (const rel of [APPS, PANEL, 'shared/ui/motion/Disintegrate.tsx']) {
      expect(srcOf(rel), `${rel} must not name a nonexistent token`)
        .not.toMatch(/(?:text|bg|border|ring)-(?:negative|positive)\b|--color-(?:negative|positive)\b/)
    }
  })
})

describe('every one of the nine now announces, through the shared owner', () => {
  it('FieldError is that owner: role=alert, the danger token, the same size', () => {
    render(<FieldError>Couldn’t load this app’s configuration.</FieldError>)
    const el = screen.getByRole('alert')
    expect(el.className, 'the live token').toContain('text-danger')
    expect(el.getAttribute('data-type'), 'same size as the div it replaces').toBe('body-s')
    expect(el.textContent).toBe('Couldn’t load this app’s configuration.')
    cleanup()
  })

  it('both files render their failures through FieldError and hand-roll none', () => {
    for (const rel of [APPS, PANEL]) {
      const c = srcOf(rel)
      expect(c, `${rel} imports the shared owner`).toMatch(/FieldError/)
      const handRolled = [...c.matchAll(/<div[^>]*data-type="body-s"[^>]*>\{(?:cfg\.err|err|guarded\.error)\}/g)]
      expect(handRolled.map((m) => m[0]), `${rel} still hand-rolls an error line`).toEqual([])
    }
  })

  it('🔴 the save guard’s own refusal is one of them', () => {
    for (const rel of [APPS, PANEL]) {
      expect(read(rel), `${rel} routes cfg.err through FieldError`)
        .toMatch(/\{cfg\.err && <FieldError>\{cfg\.err\}<\/FieldError>\}/)
    }
    const form = read('features/apps/appConfigForm.tsx')
    expect(form, 'the unloaded-form save guard').toMatch(/nothing to save yet/)
    expect(form, 'and it still refuses rather than writing').toMatch(/if \(data === undefined\)/)
  })
})

describe('the five identical rows became one', () => {
  const code = srcOf(APPS)

  it('GuardedFailure is declared once and used at every guarded surface', () => {
    expect([...code.matchAll(/function GuardedFailure\b/g)], 'exactly one declaration').toHaveLength(1)
    expect([...code.matchAll(/<GuardedFailure guarded=\{guarded\} \/>/g)].length,
      'every guarded surface adopts it').toBeGreaterThanOrEqual(5)
  })

  it('it is self-guarding, so no call site re-wraps it in its own `&&`', () => {
    const fn = code.match(/function GuardedFailure[\s\S]*?\n\}/)?.[0] ?? ''
    expect(fn, 'found GuardedFailure').not.toBe('')
    expect(fn, 'it returns null on no error').toMatch(/if \(!guarded\.error\) return null/)
    expect(code, 'and no site guards it again').not.toMatch(/guarded\.error && \(?\s*<GuardedFailure/)
  })

  it('the error and its fix-prompt stay together, as the hook says they must', () => {
    const fn = code.match(/function GuardedFailure[\s\S]*?\n\}/)?.[0] ?? ''
    expect(fn).toMatch(/<FieldError>\{guarded\.error\}<\/FieldError>/)
    expect(fn).toMatch(/<FixWithAiButton fixPrompt=\{guarded\.fixPrompt\} \/>/)
    expect(read('shared/data/useGuardedInstall.ts'), 'the doc this follows')
      .toMatch(/Rides alongside `error`/)
  })
})

describe('the allowlist shrank, which is the only direction it may move', () => {
  const allow = JSON.parse(read('shared/theme/inertUtilities.allowlist.json')) as {
    _comment: string
    allow: Record<string, string[]>
  }

  it('neither Apps file is listed any more', () => {
    expect(Object.keys(allow.allow), 'both entries are gone').not.toContain(APPS)
    expect(Object.keys(allow.allow)).not.toContain(PANEL)
  })

  it('no entry anywhere still names a negative/positive utility', () => {
    for (const [file, utils] of Object.entries(allow.allow)) {
      for (const u of utils) {
        expect(u, `${file} still allows ${u}`).not.toMatch(/-(?:negative|positive)$/)
      }
    }
  })

  it('🪤 the four unrelated entries are UNTOUCHED — they are a different change', () => {
    for (const f of [
      'features/loops/LoopCockpitPage.tsx', 'features/settings/ChatPanel.tsx',
      'features/settings/DurabilityPanel.tsx', 'features/settings/OllamaModelManager.tsx',
    ]) expect(Object.keys(allow.allow), `${f} is still listed`).toContain(f)
  })

  it('the rule that makes this the right direction is still written down', () => {
    expect(allow._comment).toMatch(/may only SHRINK/)
    expect(allow._comment, 'and the intent it recorded for these two').toMatch(/text-negative -> text-danger/)
  })
})
