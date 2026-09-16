import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

function popoverSites() {
  const out: { rel: string; line: number; portal: boolean }[] = []
  for (const abs of walk(SRC)) {
    const raw = readFileSync(abs, 'utf8')
    const src = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    for (const m of src.matchAll(/<Popover\b/g)) {
      const seg = src.slice(m.index!, m.index! + 700)
      const cut = seg.indexOf('trigger=')
      expect(cut, `${abs}: no trigger= within 700 code chars of <Popover — re-check this scan`).toBeGreaterThan(0)
      out.push({
        rel: abs.slice(SRC.length + 1),
        line: raw.slice(0, raw.indexOf('<Popover')).split('\n').length,
        portal: /\bportal\b/.test(seg.slice(0, cut)),
      })
    }
  }
  return out
}

describe('a Popover inside a clipping container must portal', () => {
  const sites = popoverSites()

  it('finds the call sites — the scan is not vacuous', () => {
    expect(sites.length, 'Popover call sites').toBeGreaterThanOrEqual(13)
  })

  it("the apps card's actions menu portals — its card clipped 56px off the bottom", () => {
    const s = sites.filter((x) => x.rel === 'features/apps/AppsSection.tsx')
    expect(s.length).toBe(1)
    expect(s[0].portal, 'without portal the card cuts off "Force uninstall" on every card').toBe(true)
  })

  it('FilterMenu portals — 202px of it fell outside the shell at 430px', () => {
    const s = sites.filter((x) => x.rel === 'shared/ui/FilterMenu.tsx')
    expect(s.length).toBe(1)
    expect(s[0].portal).toBe(true)
    const consumers = walk(SRC).filter((abs) => readFileSync(abs, 'utf8').includes('<FilterMenu'))
    expect(consumers.length, 'FilterMenu consumers that moved with this change').toBeGreaterThanOrEqual(9)
  })

  it('the non-portal call sites are exactly the three we know about', () => {
    const nonPortal = sites.filter((x) => !x.portal).map((x) => x.rel).sort()
    expect(nonPortal).toEqual([
      'features/inbox/InboxPage.tsx',
      'features/projects/ProjectsSection.tsx',
      'shared/ui/HeaderActions.tsx',
    ])
  })

  it('the primitive still documents portal as the answer to a clipping container', () => {
    expect(read('shared/ui/Popover.doc.ts')).toMatch(/overflow-clipping|clipping or/i)
  })

  it("the two fixed call sites retain their clipping safeguards", () => {
    expect(read('features/apps/AppsSection.tsx')).toMatch(/56px/)
    expect(read('shared/ui/FilterMenu.tsx')).toMatch(/<Popover\s+portal\b/)
  })
})
