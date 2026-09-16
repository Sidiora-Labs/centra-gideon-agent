import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'


const PAGES = join(process.cwd(), "src/features")

const ADOPTERS: { file: string; label: string; minRegions: number }[] = [
  { file: 'dashboard/DashboardPage.tsx', label: 'the dashboard home column', minRegions: 8 },
  { file: 'inbox/InboxPage.tsx', label: 'the inbox body', minRegions: 2 },
  { file: 'discover/DiscoverPage.tsx', label: 'the Discover hub column', minRegions: 2 },
]

const sourceOf = (file: string) => readFileSync(join(PAGES, file), 'utf8')

describe('orchestrated surface entrances', () => {
  it('finds the pages tree it scans', () => {
    expect(readdirSync(PAGES).length).toBeGreaterThan(10)
  })

  it.each(ADOPTERS)('$label stages its regions through one EntranceGroup', ({ file, minRegions }) => {
    const src = sourceOf(file)
    const groups = src.match(/<EntranceGroup\b/g) ?? []
    expect(groups, `${file} must declare exactly one <EntranceGroup>`).toHaveLength(1)
    const regions = src.match(/<EntranceRegion\b/g) ?? []
    expect(regions.length, `${file} must stage at least ${minRegions} regions`).toBeGreaterThanOrEqual(minRegions)
  })

  it.each(ADOPTERS)('$label reaches the shared primitive, never a private stagger', ({ file }) => {
    const src = sourceOf(file)
    expect(src).toMatch(/from '\.\.\/\.\.\/shared\/ui\/motion'/)
    expect(src, `${file} must not hand-roll a stagger`).not.toMatch(/\b(stagger|regionStagger)\(/)
  })

  it('regionStagger has exactly one consumer, and it is the shared primitive', () => {
    const SRC = join(process.cwd(), "src")
    const hits: string[] = []
    const walk = (dir: string) => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        const p = join(dir, e.name)
        if (e.isDirectory()) { walk(p); continue }
        if (!/\.tsx?$/.test(e.name)) continue
        if (/\bregionStagger\b/.test(readFileSync(p, 'utf8'))) hits.push(p.slice(SRC.length + 1))
      }
    }
    walk(SRC)
    expect(hits.sort()).toEqual([
      'shared/theme/motion.test.ts',
      'shared/theme/motion.ts',
      'features/surfaceEntranceAdoption.test.ts',
      'shared/ui/motion/Entrance.tsx',
    ])
  })
})
