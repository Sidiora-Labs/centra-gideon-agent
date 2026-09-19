import { describe, expect, it, vi } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { ROUTES, THEMES, VIEW_ROUTES } from '../../../e2e/routes'
import { VISUAL_BASELINE_PLATFORMS } from '../../../playwright.config'
import { lazyRoute, preloadRoute } from './routePreload'

const ROOT = process.cwd()
const APP = readFileSync(join(ROOT, 'src/app/shell/App.tsx'), 'utf8')
const HELPERS = readFileSync(join(ROOT, 'e2e/helpers.ts'), 'utf8')
const VISUAL_SPEC = readFileSync(join(ROOT, 'e2e/visual.spec.ts'), 'utf8')
const BASELINES = join(ROOT, 'e2e/__screenshots__/visual.spec.ts')

describe('visual route loading', () => {
  it('shares one import between React lazy and every preload request', async () => {
    const load = vi.fn(async () => ({ default: () => null }))
    lazyRoute('visual-rail-once', load)

    await expect(Promise.all([
      preloadRoute('visual-rail-once'),
      preloadRoute('#/visual-rail-once/sub?view=graph'),
    ])).resolves.toEqual([true, true])
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('registers every split page under the route that renders it', () => {
    const declared = [...APP.matchAll(/const (\w+) = lazyRoute\('([^']+)',/g)]
    expect(declared.length).toBeGreaterThan(20)
    expect(APP).not.toMatch(/=\s*lazy\(/)
    expect(APP).toContain('installRoutePreload()')

    const registered = new Map(declared.map(([, component, route]) => [component, route]))
    const table = APP.slice(APP.indexOf('const pageComponents'), APP.indexOf('function renderPage'))
    const mismatched: string[] = []
    let covered = 0
    for (const [, route, component] of table.matchAll(/^\s*'?([\w-]+)'?:\s*(\w+),/gm)) {
      const preload = registered.get(component)
      if (preload === undefined) continue
      covered++
      if (preload !== route) mismatched.push(`${component}: ${preload} != ${route}`)
    }
    expect(covered).toBeGreaterThan(20)
    expect(mismatched).toEqual([])
  })

  it('settles a loaded shell and its finite motion before the final paint', () => {
    const body = HELPERS.slice(HELPERS.indexOf('export async function gotoRoute'), HELPERS.indexOf('export async function settleEntranceAnimations'))
    const paint = 'requestAnimationFrame(() => requestAnimationFrame('
    const ordered = [
      'page.goto(',
      'preloadRoute(page, route)',
      'assertShellMounted(page)',
      'fonts?.ready',
      "waitForLoadState('networkidle'",
      paint,
      'settleEntranceAnimations(page)',
    ].map((token) => body.indexOf(token))
    expect(ordered.every((position) => position >= 0)).toBe(true)
    expect(ordered).toEqual([...ordered].sort((a, b) => a - b))
    expect(body.lastIndexOf(paint)).toBeGreaterThan(body.indexOf('settleEntranceAnimations(page)'))
    expect(body).not.toContain('waitForTimeout(')
  })
})

describe('visual golden census', () => {
  const surfaces = [...ROUTES, ...VIEW_ROUTES]
  const surfaceIds = new Set(surfaces.map(({ route, id }) => id ?? route))
  const files = readdirSync(BASELINES).filter((name) => name.endsWith('.png')).sort()
  const parsed = files.map((name) => {
    const stem = name.slice(0, -'.png'.length)
    const separator = stem.lastIndexOf('-')
    if (separator < 1) throw new Error(`unrecognised visual baseline name: ${name}`)
    const platform = stem.slice(separator + 1)
    const key = stem.slice(0, separator)
    const theme = THEMES.find((candidate) => key.endsWith(`-${candidate}`))
    if (!theme) throw new Error(`unrecognised visual baseline theme: ${name}`)
    return { surface: key.slice(0, -`-${theme}`.length), platform }
  })

  it('derives the browser case count from routes and themes', () => {
    const cases = surfaces.flatMap(({ route, id }) => THEMES.map((theme) => `${id ?? route}-${theme}`))
    expect(new Set(cases).size).toBe(cases.length)
    expect(VISUAL_SPEC).toContain('const VISUAL_ROUTES = [...ROUTES, ...VIEW_ROUTES]')
    expect(VISUAL_SPEC).toContain("test.describe.configure({ mode: 'serial' })")
    expect(VISUAL_SPEC).toContain("test.use({ reducedMotion: 'reduce' })")
  })

  it('keeps the committed platform set closed to Darwin', () => {
    const platforms = [...new Set(parsed.map(({ platform }) => platform))].sort()
    expect(platforms).toEqual([...VISUAL_BASELINE_PLATFORMS])
  })

  it('derives the committed count and requires a complete theme pair per captured surface', () => {
    const capturedSurfaces = new Set(parsed.map(({ surface }) => surface))
    for (const surface of capturedSurfaces) expect(surfaceIds.has(surface), `${surface} is not in the visual manifest`).toBe(true)

    const expected = [...capturedSurfaces].flatMap((surface) =>
      VISUAL_BASELINE_PLATFORMS.flatMap((platform) =>
        THEMES.map((theme) => `${surface}-${theme}-${platform}.png`),
      ),
    ).sort()
    expect(files).toEqual(expected)
    expect(files).toHaveLength(capturedSurfaces.size * THEMES.length * VISUAL_BASELINE_PLATFORMS.length)
  })
})
