import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const WEB = process.cwd()
const read = (p: string) => readFileSync(join(WEB, p), 'utf8')

const ROUTES_SRC = read('e2e/routes.ts')
const VISUAL_SRC = read('e2e/visual.spec.ts')
const A11Y_SRC = read('e2e/a11y.spec.ts')

interface Entry { route: string; id?: string }

function entries(exportName: string): Entry[] {
  const block = ROUTES_SRC.match(new RegExp(`export const ${exportName}: RouteEntry\\[\\] = \\[(.*?)\\n\\]`, 's'))
  expect(block, `could not locate the ${exportName} literal in e2e/routes.ts`).toBeTruthy()
  return [...block![1].matchAll(/route: '([^']+)'(?:[^\n]*?id: '([^']+)')?/g)]
    .map((m) => ({ route: m[1], id: m[2] }))
}

function navRoutes(): string[] {
  const block = ROUTES_SRC.match(/export const ROUTES: RouteEntry\[\] = \[(.*?)\n\]/s)
  expect(block, 'could not locate the ROUTES literal').toBeTruthy()
  return [...block![1].matchAll(/route: '([^']+)'/g)].map((m) => m[1])
}

const VIEW_ROUTES = entries('VIEW_ROUTES')
const GRAPH_ROUTE = 'knowledge?view=graph'

describe('the knowledge graph is reachable as a harness route', () => {
  it('parses a non-empty sub-view list — the vacuity floor', () => {
    expect(VIEW_ROUTES.length, 'VIEW_ROUTES parsed as empty — the matcher is broken').toBeGreaterThan(0)
    expect(navRoutes().length, 'the ROUTES matcher must still find the nav routes').toBeGreaterThan(10)
  })

  it('enumerates the graph', () => {
    expect(VIEW_ROUTES.map((e) => e.route), `#/${GRAPH_ROUTE} must be in the harness manifest`)
      .toContain(GRAPH_ROUTE)
  })

  it('gives every query-param route a filesystem-safe artifact id', () => {
    for (const { route, id } of VIEW_ROUTES) {
      expect(id, `${route} carries a query string, so it needs an explicit id`).toBeTruthy()
      expect(id!, `${id} must be filesystem-safe`).toMatch(/^[a-z0-9-]+$/)
    }
  })

  it('keeps every artifact name unique across all three lists', () => {
    const all = [...entries('ROUTES'), ...VIEW_ROUTES].map((e) => e.id ?? e.route)
    const panels = (ROUTES_SRC.match(/export const SETTINGS_PANELS = \[([\s\S]*?)\] as const/) ?? [])[1] ?? ''
    all.push(...[...panels.matchAll(/'([a-z-]+)'/g)].map((m) => `settings/${m[1]}`))
    expect(all.length, 'the id sweep found nothing to check').toBeGreaterThan(10)
    expect(new Set(all).size, `duplicate harness artifact ids: ${all.join(', ')}`).toBe(all.length)
  })

  it('is consumed by BOTH harness specs, so the list is not an inert declaration', () => {
    for (const [name, src] of [['visual.spec.ts', VISUAL_SRC], ['a11y.spec.ts', A11Y_SRC]] as const) {
      expect(src, `${name} must iterate VIEW_ROUTES`).toMatch(/\.\.\.VIEW_ROUTES/)
      expect(src, `${name} must name its artifact by id, so no '?' reaches a filename`)
        .toMatch(/id \?\? route/)
    }
  })

  it('resolves to the graph — every link of the chain, from source', () => {
    const [path, qs] = GRAPH_ROUTE.split('?')
    const [param, value] = qs.split('=')

    expect(navRoutes(), `${path} must be a nav route`).toContain(path)

    const hash = read('src/app/shell/useHashRoute.ts')
    expect(hash, 'the hash router must parse a query string').toMatch(/new URLSearchParams\(qs\)/)
    expect(hash, 'and must resolve the route from the path only').toMatch(/segs\[0\] \|\| fallback/)

    const app = read('src/app/shell/App.tsx')
    expect(app).toMatch(new RegExp(`case '${path}': return <KnowledgeSection \\{\\.\\.\\.r\\} />`))

    expect(read('src/features/knowledge/KnowledgeSection.tsx'))
      .toMatch(/<KnowledgeListPage[\s\S]*?query=\{query\} setQuery=\{setQuery\}/)

    const page = read('src/features/knowledge/KnowledgeListPage.tsx')
    const decl = new RegExp(`useQueryParam\\(query, setQuery, '${param}', '([a-z]+)'`).exec(page)
    expect(decl, `KnowledgeListPage must read the '${param}' query param`).toBeTruthy()
    expect(decl![1], `the default view must NOT be '${value}', or this route would be redundant`)
      .not.toBe(value)

    expect(page, `view === '${value}' must render the graph`)
      .toMatch(new RegExp(`view === '${value}' &&[\\s\\S]{0,400}<KnowledgeGraph`))
  })
})
