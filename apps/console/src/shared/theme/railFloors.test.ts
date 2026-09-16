import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

const TIGHTENED: [string, RegExp, number][] = [
  [
    'features/settings/configReadNotFabricated.test.ts',
    /the decorating fallbacks in these five files, measured'\)\s*\.toBeGreaterThanOrEqual\((\d+)\)/,
    33,
  ],
  ['shared/ui/requiredFieldMarked.test.tsx', /population must still be visible to this rail'\)\.toBeGreaterThanOrEqual\((\d+)\)/, 20],
  ['shared/theme/controlNameFloor.test.ts', /expected the inline rename\/edit inputs'\)\.toBeGreaterThanOrEqual\((\d+)\)/, 18],
  ['shared/ui/escapeDismissContract.test.tsx', /scrim-bearing overlays'\)\.toBeGreaterThanOrEqual\((\d+)\)/, 10],
]

describe('a floor that stands for a population sits at the population', () => {
  for (const [rel, re, measured] of TIGHTENED) {
    it(`${rel.split('/').pop()} floors at its measured ${measured}`, () => {
      const m = read(rel).match(re)
      expect(m, `${rel} must still carry the floor this rail is about`).not.toBeNull()
      expect(Number(m![1]), 'lowering this is how a rail stops guarding').toBeGreaterThanOrEqual(measured)
    })
  }

  it('none of the four reverted to a loose `toBeGreaterThan`', () => {
    expect(read('shared/ui/requiredFieldMarked.test.tsx')).not.toMatch(/population must still be visible to this rail'\)\.toBeGreaterThan\(/)
    expect(read('shared/ui/escapeDismissContract.test.tsx')).not.toMatch(/scrim-bearing overlays'\)\.toBeGreaterThan\(/)
  })

  it('the vacuity guards were deliberately LEFT loose', () => {
    expect(read('features/dashboard/widgets/widgetsReachable.test.ts'), 'a directory listing guard stays loose')
      .toMatch(/expect\(modules\.length\)\.toBeGreaterThan\(5\)/)
    expect(read('shared/ui/loadErrorState.test.tsx'), 'a tree-walk guard stays loose')
      .toMatch(/walk\(SRC\)\.length, 'the walker must find the tree'\)\.toBeGreaterThan\(200\)/)
  })

  it('the quality margins were not touched', () => {
    const contrast = read('shared/theme/schemeContrast.test.ts')
    expect(contrast).toMatch(/toBeGreaterThanOrEqual\(AA\)/)
    expect(contrast, 'no scheme contrast may be pinned to its current measurement').not.toMatch(/toBeGreaterThanOrEqual\(1[0-9]\.\d/)
  })

  it('the audit itself is repeatable — every floor in the suite is still countable', () => {
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.test\.tsx?$/.test(n) ? [p] : []
      })
    const floors = walk(SRC).flatMap((abs) =>
      [...readFileSync(abs, 'utf8').matchAll(/\.toBeGreaterThan(?:OrEqual)?\(\d+\)/g)])
    expect(floors.length, 'the floor census must still find the suite').toBeGreaterThanOrEqual(100)
  })
})
