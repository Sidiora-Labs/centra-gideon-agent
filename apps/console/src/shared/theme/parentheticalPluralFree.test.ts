import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

const HEDGE = /(?:\}|\d)[^`'"()]{0,40}?\s[a-z][a-z-]*\((?:s|es)\)/
const stripComments = (t: string) =>
  t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) sources(p, out)
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p)
  }
  return out
}

const ONCE_EXEMPT_NOW_CLEAN = [
  'features/learning/HealthPanel.tsx',
  'features/learning/LearningPage.tsx',
  'features/learning/learningMeta.ts',
  'features/settings/DurabilityPanel.tsx',
  'features/workflows/IntrospectPanel.tsx',
  'shared/ui/DegradedChip.tsx',
  'features/onboarding/ImportStep.tsx',
  'shared/ui/genui/registry.ts',
]

const BACKEND_VERBATIM_NOTE =
  'strings composed in Python and rendered verbatim are excluded by construction — they never appear ' +
  'as literals in this tree, so the scan cannot see them and must not pretend to.'

function offenders(): string[] {
  const out: string[] = []
  for (const abs of sources(SRC)) {
    const rel = abs.slice(SRC.length + 1).replace(/\\/g, '/')
    stripComments(readFileSync(abs, 'utf8')).split('\n').forEach((line, i) => {
      if (HEDGE.test(line) && !/http\(s\)/.test(line)) {
        out.push(`${rel}:${i + 1}  ${line.trim().slice(0, 90)}`)
      }
    })
  }
  return out
}

describe('no composed sentence hedges its own count', () => {
  it('the detector still works, in both directions', () => {
    expect(HEDGE.test('`${week.produced_total} proposal(s) filed`'), 'must catch an interpolation').toBe(true)
    expect(HEDGE.test('over 4 pass(es), and'), 'must catch (es) after a literal digit').toBe(true)
    expect(HEDGE.test('`${n} of ${files.length} file(s) to this point?`'), 'must catch the second count').toBe(true)
    expect(HEDGE.test('setBusy((s) => new Set(s).add(id))'), 'Set(s).add is code').toBe(false)
    expect(HEDGE.test('onClick={() => open(s)}'), 'open(s) is code').toBe(false)
    expect(HEDGE.test('`${location.pathname}#/chat/${encodeURIComponent(s)}`'), 'encodeURIComponent is code').toBe(false)
    expect(HEDGE.test('return `${m}:${String(s).padStart(2, "0")}`'), 'String(s) is code').toBe(false)
    expect(HEDGE.test('// allow relative, anchors, mailto/tel, http(s)')).toBe(false)
  })

  it('the scan reads the tree (a scan over nothing reports everything clean)', () => {
    const all = sources(SRC)
    expect(all.length, 'no sources found under src/').toBeGreaterThan(400)
    for (const rel of ONCE_EXEMPT_NOW_CLEAN) {
      expect(
        all.some((a) => a.endsWith(rel)),
        `${rel} was once exempt and the walk no longer sees it — stale entry or broken walk`,
      ).toBe(true)
    }
  })

  it('no file composes a hedged plural', () => {
    expect(
      offenders(),
      'The count is already in hand at each of these. Write the sentence:\n' +
        "  `${n} thing${n === 1 ? '' : 's'}`  — the form 156 other sites in this tree already use.\n" +
        'Do NOT add a local `plural()` helper; two page-local copies already exist and a third makes ' +
        'the eventual consolidation bigger.\n' +
        BACKEND_VERBATIM_NOTE,
    ).toEqual([])
  })

  it('🏁 the rail exempts NOTHING — the end state the interim list was aiming at', () => {
    const src = readFileSync(join(SRC, 'shared/theme/parentheticalPluralFree.test.ts'), 'utf8')
    const code = stripComments(src)
    expect(code, 'no file may be skipped by name — convert the site, do not exempt it')
      .not.toMatch(/\.includes\(rel\)\s*\)\s*continue/)
    expect(ONCE_EXEMPT_NOW_CLEAN.length, 'the family\'s hardest members stay named').toBe(8)
    const found = offenders()
    for (const rel of ONCE_EXEMPT_NOW_CLEAN) {
      expect(found.some((o) => o.startsWith(rel)), `${rel} regressed a hedged plural`).toBe(false)
    }
  })
})
