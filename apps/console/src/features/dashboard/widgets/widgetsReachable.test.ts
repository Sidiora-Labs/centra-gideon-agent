import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'


const WIDGETS = join(process.cwd(), "src/features/dashboard/widgets")
const SRC = join(process.cwd(), "src")

function allSources(): Array<{ path: string; text: string }> {
  const out: Array<{ path: string; text: string }> = []
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name)
      if (e.isDirectory()) { walk(p); continue }
      if (!/\.tsx?$/.test(e.name)) continue
      out.push({ path: p, text: readFileSync(p, 'utf8') })
    }
  }
  walk(SRC)
  return out
}

describe('dashboard widget modules', () => {
  const sources = allSources()

  it('scans a real tree (guards against a silently-empty sweep)', () => {
    expect(sources.length).toBeGreaterThan(100)
    expect(sources.some((s) => s.path.endsWith(join('widgets', 'kit.tsx')))).toBe(true)
  })

  it('every module is referenced from outside its own file', () => {
    const modules = readdirSync(WIDGETS)
      .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f))
    expect(modules.length).toBeGreaterThan(5)

    const orphans: string[] = []
    for (const file of modules) {
      const name = file.replace(/\.tsx?$/, '')
      const own = join(WIDGETS, file)
      const referenced = sources.some((s) =>
        s.path !== own && !/\.test\.tsx?$/.test(s.path) &&
        new RegExp(`\\b${name}\\b`).test(s.text))
      if (!referenced) orphans.push(file)
    }
    expect(
      orphans,
      'These widget modules have no importer anywhere in src/, so they are tree-shaken out of ' +
        'every build — dead code that still costs review attention and drifts against its live ' +
        `siblings:\n  ${orphans.join('\n  ')}`,
    ).toEqual([])
  })

  it('the widgets DashboardPage mounts are among them', () => {
    const page = readFileSync(join(SRC, 'features/dashboard/DashboardPage.tsx'), 'utf8')
    for (const w of ['TasksWidget', 'ScheduleWidget']) {
      expect(page, `DashboardPage should still mount ${w}`).toMatch(new RegExp(`\\b${w}\\b`))
      expect(readdirSync(WIDGETS)).toContain(`${w}.tsx`)
    }
  })
})
