import { Suspense } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { installRoutePreload, lazyRoute, preloadRoute } from './routePreload'

const SRC = readFileSync(join(process.cwd(), 'src/app/shell/App.tsx'), 'utf8')

describe('a route chunk can be resolved before anything waits on it', () => {
  it('starts the same import the router would, and reports that it landed', async () => {
    const load = vi.fn(async () => ({ default: () => <p>tools landed</p> }))
    lazyRoute('preload-tools', load)

    expect(load).not.toHaveBeenCalled()
    await expect(preloadRoute('preload-tools')).resolves.toBe(true)
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('resolves the module once, however many deep links ask for it', async () => {
    const load = vi.fn(async () => ({ default: () => <p>inbox landed</p> }))
    lazyRoute('preload-inbox', load)

    const [first, second] = await Promise.all([preloadRoute('preload-inbox'), preloadRoute('preload-inbox')])
    await preloadRoute('#/preload-inbox')

    expect([first, second]).toEqual([true, true])
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('warms the chunk the lazy component then renders from', async () => {
    const Page = lazyRoute('preload-knowledge', async () => ({ default: () => <p>knowledge landed</p> }))
    await expect(preloadRoute('preload-knowledge')).resolves.toBe(true)

    render(<Suspense fallback={<p>loading</p>}><Page /></Suspense>)
    await waitFor(() => expect(screen.getByText('knowledge landed')).toBeInTheDocument())
  })

  it('reads the route out of a deep link the way the hash router does', async () => {
    const load = vi.fn(async () => ({ default: () => <p>settings landed</p> }))
    lazyRoute('preload-settings', load)

    await expect(preloadRoute('preload-settings/account')).resolves.toBe(true)
    await expect(preloadRoute('#/preload-settings/guardrails')).resolves.toBe(true)
    expect(load).toHaveBeenCalledTimes(1)

    const host = vi.fn(async () => ({ default: () => <p>host landed</p> }))
    lazyRoute('preload-app', host)
    await expect(preloadRoute('preload-app/shell/e2e-ui-fixture')).resolves.toBe(true)

    const graph = vi.fn(async () => ({ default: () => <p>graph landed</p> }))
    lazyRoute('preload-graph', graph)
    await expect(preloadRoute('preload-graph?view=graph')).resolves.toBe(true)
  })

  it('says no for a route that owns no chunk, instead of throwing at the caller', async () => {
    await expect(preloadRoute('chat')).resolves.toBe(false)
    await expect(preloadRoute('')).resolves.toBe(false)
    await expect(preloadRoute('#/nothing/at/all')).resolves.toBe(false)
  })

  it('reports a chunk that failed to load rather than rejecting into the test', async () => {
    lazyRoute('preload-broken', () => Promise.reject(new Error('transform failed')))
    await expect(preloadRoute('preload-broken')).resolves.toBe(false)
  })

  it('is reachable from the page, which is how a browser test reaches it', async () => {
    installRoutePreload()
    const hook = (window as unknown as { __gideon_preload_route?: (path: string) => Promise<boolean> })
      .__gideon_preload_route
    expect(typeof hook).toBe('function')

    lazyRoute('preload-terminal', async () => ({ default: () => <p>terminal landed</p> }))
    await expect(hook?.('preload-terminal')).resolves.toBe(true)
    await expect(hook?.('preload-missing')).resolves.toBe(false)
  })
})

describe('every lazy page stays reachable through the preload hook', () => {
  const declared = [...SRC.matchAll(/const (\w+) = lazyRoute\('([^']+)',/g)]

  it('declares its split pages through lazyRoute, never a bare lazy()', () => {
    expect(declared.length, 'App.tsx no longer declares split pages here — this rail measures nothing')
      .toBeGreaterThan(20)
    expect(SRC).toMatch(/import \{ installRoutePreload, lazyRoute \} from '\.\/routePreload'/)
    expect(SRC, 'a bare lazy() page would never be warmed').not.toMatch(/=\s*lazy\(/)
    expect(SRC, 'the hook must be installed for a deep-link test to reach it').toContain('installRoutePreload()')
  })

  it('registers each page under the route id the shell renders it for', () => {
    const table = SRC.slice(SRC.indexOf('const pageComponents'), SRC.indexOf('function renderPage'))
    expect(table.length, 'the page table moved — this rail measures nothing').toBeGreaterThan(200)
    const routed = new Map(declared.map(([, name, route]) => [name, route]))
    const mismatched: string[] = []
    let covered = 0
    for (const [, key, component] of table.matchAll(/^\s*'?([\w-]+)'?:\s*(\w+),/gm)) {
      const route = routed.get(component)
      if (route === undefined) continue
      covered++
      if (route !== key) mismatched.push(`${component} renders #/${key} but preloads '${route}'`)
    }
    expect(mismatched, 'a deep link would warm the wrong chunk').toEqual([])
    expect(covered, 'no lazy page was matched against the table — vacuous').toBeGreaterThan(20)
  })

  it('covers the routes the deep-link suite walks', () => {
    const ids = new Set(declared.map(([, , route]) => route))
    const walked = readFileSync(join(process.cwd(), 'e2e/routes.ts'), 'utf8')
    const missing = [...walked.matchAll(/\{ route: '([\w-]+)(?:[/?][^']*)?'/g)]
      .map((m) => m[1])
      .filter((route) => route !== 'chat' && !ids.has(route))
    expect(missing, 'these walked routes are split but unwarmed').toEqual([])
  })
})
