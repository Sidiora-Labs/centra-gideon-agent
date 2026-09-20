import { describe, expect, it, vi } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { render } from '@testing-library/react'
import { NON_NAV_ROUTES, ROUTES, SETTINGS_ROUTES, THEMES, VIEW_ROUTES } from '../../../e2e/routes'
import { VISUAL_BASELINE_PLATFORMS } from '../../../playwright.config'
import { lazyRoute, preloadRoute } from './routePreload'
import { Loading, Skeleton } from '../../shared/ui/ListScaffold'

const ROOT = process.cwd()
const APP = readFileSync(join(ROOT, 'src/app/shell/App.tsx'), 'utf8')
const HELPERS = readFileSync(join(ROOT, 'e2e/helpers.ts'), 'utf8')
const VISUAL_SPEC = readFileSync(join(ROOT, 'e2e/visual.spec.ts'), 'utf8')
const PACKAGE = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8')) as { scripts: Record<string, string> }
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

  it('waits for the shared pending-read signal before settling a capture', () => {
    const body = HELPERS.slice(HELPERS.indexOf('export async function gotoRoute'), HELPERS.indexOf('export async function settleEntranceAnimations'))
    const network = body.indexOf("waitForLoadState('networkidle'")
    const waiting = body.indexOf('VISUAL_WAITING_SELECTOR')
    const settle = body.indexOf('settleEntranceAnimations(page)')
    expect(network).toBeGreaterThanOrEqual(0)
    expect(waiting).toBeGreaterThan(network)
    expect(settle).toBeGreaterThan(waiting)

    const { container, rerender } = render(createElement(Skeleton, { className: 'h-4 w-20' }))
    expect(container.querySelector('[data-visual-state="waiting"]')).toBeTruthy()
    rerender(createElement(Loading, { what: 'projects' }))
    expect(container.querySelector('[data-visual-state="waiting"]')).toBeTruthy()
  })

  it('keeps every shared skeleton and route spinner in the waiting census', () => {
    const scaffold = readFileSync(join(ROOT, 'src/shared/ui/ListScaffold.tsx'), 'utf8')
    expect(scaffold.match(/data-visual-state="waiting"/g)).toHaveLength(3)
    expect(APP.slice(APP.indexOf('function PageFallback'), APP.indexOf('const pageComponents'))).toContain('data-visual-state="waiting"')
    expect(APP.slice(APP.indexOf('if (!loaded)'), APP.indexOf("if (route === 'onboarding'"))).toContain('data-visual-state="waiting"')
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
    expect(VISUAL_SPEC).toContain("{ tag: '@visual' }")
  })

  it('runs state-changing specs before the isolated visual partition', () => {
    expect(PACKAGE.scripts.e2e).toBe('playwright test --grep-invert @visual && playwright test --grep @visual')
    expect(PACKAGE.scripts['e2e:update']).toBe('playwright test e2e/visual.spec.ts --update-snapshots')
  })

  it('checks pristine flywheel state before comparing pixels', () => {
    const screenshot = HELPERS.slice(HELPERS.indexOf('export async function expectRouteScreenshot'))
    expect(screenshot.indexOf('assertPristineFlywheel(page)')).toBeLessThan(screenshot.indexOf('toHaveScreenshot'))
    expect(HELPERS).toContain("page.request.get('/api/learning/health?days=7')")
    expect(HELPERS).toContain('if (typeof measured !== \'number\') return')
    expect(HELPERS).toContain(').toBe(0)')
  })

  it('keeps the committed platform set equal to the declared platforms', () => {
    const platforms = [...new Set(parsed.map(({ platform }) => platform))].sort()
    expect(platforms).toEqual([...VISUAL_BASELINE_PLATFORMS])
  })

  it('requires every declared platform to capture every manifest surface and theme', () => {
    const expected = [...surfaceIds].flatMap((surface) =>
      VISUAL_BASELINE_PLATFORMS.flatMap((platform) =>
        THEMES.map((theme) => `${surface}-${theme}-${platform}.png`),
      ),
    ).sort()
    expect(files).toEqual(expected)
    expect(files).toHaveLength(40)
    expect(files).toHaveLength(surfaceIds.size * THEMES.length * VISUAL_BASELINE_PLATFORMS.length)
  })
})

describe('axe route census', () => {
  it('pins every route and theme scanned by axe', () => {
    const routeCases = (ROUTES.length + SETTINGS_ROUTES.length + VIEW_ROUTES.length + NON_NAV_ROUTES.length) * THEMES.length
    expect(routeCases).toBe(124)
  })
})
