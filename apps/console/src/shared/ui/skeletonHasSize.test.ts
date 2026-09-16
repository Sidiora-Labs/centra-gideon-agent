import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

const strip = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

function walk(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir)) {
    const abs = join(dir, e)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.tsx$/.test(e) && !/\.test\.tsx$/.test(e)) out.push(abs)
  }
  return out
}

const FILES = walk(SRC)

describe('every skeleton placeholder has a size', () => {
  it('no call site renders a bare <Skeleton /> — it would be invisible', () => {
    const bare: string[] = []
    for (const abs of FILES) {
      const body = strip(readFileSync(abs, 'utf8'))
      for (const _m of body.matchAll(/<Skeleton\s*\/>/g)) bare.push(abs.slice(SRC.length + 1))
    }
    expect(bare, `these render a 0px invisible element:\n${bare.join('\n')}`).toEqual([])
  })

  it('and the population is real, so the assertion above is not vacuous', () => {
    const total = FILES.reduce(
      (n, abs) => n + [...strip(readFileSync(abs, 'utf8')).matchAll(/<Skeleton\b/g)].length,
      0,
    )
    expect(total, 'Skeleton call sites outside comments').toBeGreaterThanOrEqual(45)
  })

  it('`className` is REQUIRED on the primitive, so a sizeless one cannot compile', () => {
    const kit = readFileSync(join(SRC, 'shared/ui/ListScaffold.tsx'), 'utf8')
    const decl = strip(kit).match(/export function Skeleton\([^)]*\)/)?.[0] ?? ''
    expect(decl, 'the Skeleton declaration must be found before it is checked').not.toBe('')
    expect(decl, 'an optional className is what allowed a 0px skeleton').not.toMatch(/className\?/)
    expect(decl, 'nor may it default to empty').not.toMatch(/className\s*=\s*''/)
    expect(decl).toMatch(/className:\s*string/)
  })

  it('the three panels that rendered nothing now render a shaped, announced placeholder', () => {
    const cases: [string, RegExp][] = [
      ['features/workflows/LedgerRailsPanel.tsx', /<FormSkeleton sections=\{1\} rows=\{3\} title=\{false\} \/>/],
      ['features/workflows/IntrospectPanel.tsx', /<FormSkeleton sections=\{1\} rows=\{4\} title=\{false\} \/>/],
      ['features/workflows/OutboxPanel.tsx', /<ListSkeleton rows=\{3\} \/>/],
    ]
    for (const [rel, re] of cases) {
      expect(strip(readFileSync(join(SRC, rel), 'utf8')), `${rel} must render a shaped skeleton`).toMatch(re)
    }
  })

  it('the two Outbox detail placeholders are SIZED atoms, deliberately not shaped', () => {
    const src = strip(readFileSync(join(SRC, 'features/workflows/OutboxPanel.tsx'), 'utf8'))
    expect(src).toMatch(/<Skeleton className="h-24 w-full" \/>/)
    expect(src).toMatch(/fallback=\{<Skeleton className="h-full w-full" \/>\}/)
  })
})
