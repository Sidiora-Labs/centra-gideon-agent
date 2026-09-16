import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const EXEMPT_FROM_THE_HARNESS: Record<string, string> = {
  loop: 'loop detail — needs a loop to address; also carries the logged overflowing-control-row taste call',
  loops: 'loop history/planning sub-route (#/loops/<id>) — needs a loop id to render a record',
  code: 'code sub-route of a loop (#/code/<id>) — needs a loop id to render a record',
}

const WEB = process.cwd()

function navIds(): string[] {
  const src = readFileSync(join(WEB, 'src/app/shell/App.tsx'), 'utf8')
  const block = src.match(/const NAV: NavItem\[\] = \[(.*?)\n\]/s)
  if (!block) throw new Error('could not locate the NAV literal in App.tsx')
  return [...block[1].matchAll(/\{\s*id: '([^']+)'/g)].map((m) => m[1])
}

function manifestRoutes(): string[] {
  const src = readFileSync(join(WEB, 'e2e/routes.ts'), 'utf8')
  const block = src.match(/export const ROUTES: RouteEntry\[\] = \[(.*?)\n\]/s)
  if (!block) throw new Error('could not locate the ROUTES literal in e2e/routes.ts')
  return [...block[1].matchAll(/route: '([^']+)'/g)].map((m) => m[1])
}

function nonNavRoutes(): string[] {
  const src = readFileSync(join(WEB, 'e2e/routes.ts'), 'utf8')
  const block = src.match(/export const NON_NAV_ROUTES: RouteEntry\[\] = \[(.*?)\n\]/s)
  if (!block) throw new Error('could not locate the NON_NAV_ROUTES literal in e2e/routes.ts')
  return [...block[1].matchAll(/route: '([^']+)'/g)].map((m) => m[1])
}

function scannedRoutes(): string[] {
  return [...manifestRoutes(), ...nonNavRoutes()]
}

function routableExtras(): string[] {
  const src = readFileSync(join(WEB, 'src/app/shell/App.tsx'), 'utf8')
  const block = src.match(/const ROUTABLE = new Set\(\[(.*?)\]\)/s)
  if (!block) throw new Error('could not locate the ROUTABLE literal in App.tsx')
  return [...block[1].matchAll(/'([^']+)'/g)].map((m) => m[1])
}

describe('e2e route manifest vs NAV', () => {
  it('parses both lists (guards against a silently-empty sweep)', () => {
    expect(navIds().length).toBeGreaterThan(10)
    expect(manifestRoutes().length).toBeGreaterThan(10)
  })

  it('covers every nav route', () => {
    const missing = navIds().filter((id) => !manifestRoutes().includes(id))
    expect(
      missing,
      'These nav routes are absent from web/e2e/routes.ts, so they get NO axe scan and NO ' +
        'visual baseline — the harness cannot report a page it does not know about.',
    ).toEqual([])
  })

  it('lists no route that is not in NAV', () => {
    const stale = manifestRoutes().filter((r) => !navIds().includes(r))
    expect(stale, 'web/e2e/routes.ts lists routes that NAV no longer has').toEqual([])
  })

  describe('non-nav routable pages are scanned or declared', () => {
    it('parses the ROUTABLE extras (guards against a silently-empty sweep)', () => {
      expect(routableExtras().length).toBeGreaterThan(3)
      expect(nonNavRoutes().length).toBeGreaterThan(0)
      expect(Object.keys(EXEMPT_FROM_THE_HARNESS).length).toBeGreaterThan(0)
    })

    it('every non-nav routable page is either scanned or declared exempt', () => {
      const nav = navIds()
      const segments = new Set(scannedRoutes().map((r) => r.split('/')[0]))
      const undeclared = routableExtras().filter(
        (r) => !nav.includes(r) && !segments.has(r) && !(r in EXEMPT_FROM_THE_HARNESS),
      )
      expect(
        undeclared,
        'These routes are reachable in the SPA (they are in App.tsx ROUTABLE) but are in no nav\n' +
          'tile, no e2e route manifest, and no exemption — so they get NO axe scan and NO visual\n' +
          'baseline, and nothing says that was intended. Add them to NON_NAV_ROUTES in\n' +
          'web/e2e/routes.ts to scan them, or to EXEMPT_FROM_THE_HARNESS above with the reason\n' +
          'they cannot be. "It has no nav tile" is NOT such a reason — renderPage serves every\n' +
          'ROUTABLE id off a bare #/<id>, so state what the harness cannot supply (a record id,\n' +
          'an installed app) or scan it.',
      ).toEqual([])
    })

    it('declares no exemption for a route that no longer exists', () => {
      const routable = new Set(routableExtras())
      const stale = Object.keys(EXEMPT_FROM_THE_HARNESS).filter((r) => !routable.has(r))
      expect(stale, 'EXEMPT_FROM_THE_HARNESS names routes App.tsx no longer routes to').toEqual([])
    })

    it('exempts no route that IS already scanned', () => {
      const scanned = scannedRoutes()
      const both = Object.keys(EXEMPT_FROM_THE_HARNESS).filter((r) => scanned.includes(r))
      expect(both, 'these routes are declared exempt AND scanned — drop the exemption').toEqual([])
    })

    it('scans no non-nav route the shell does not route to', () => {
      const routable = new Set(routableExtras())
      const firstSegment = (r: string) => r.split('?')[0].split('/')[0]
      const stale = nonNavRoutes().filter((r) => !routable.has(firstSegment(r)))
      expect(stale, 'NON_NAV_ROUTES names routes App.tsx no longer routes to').toEqual([])
    })

    it('keeps NON_NAV_ROUTES disjoint from NAV and from ROUTES', () => {
      const nav = navIds()
      const manifest = manifestRoutes()
      const overlap = nonNavRoutes().filter((r) => nav.includes(r) || manifest.includes(r))
      expect(
        overlap,
        'these routes are in NON_NAV_ROUTES and also in NAV/ROUTES — a nav route belongs in\n' +
          'ROUTES, which is what also gives it a visual baseline',
      ).toEqual([])
    })

    it('every exemption carries a real reason', () => {
      const thin = Object.entries(EXEMPT_FROM_THE_HARNESS)
        .filter(([, why]) => why.trim().length < 30)
        .map(([r]) => r)
      expect(thin, 'these exemptions have no stated reason').toEqual([])
    })
  })
})


function routeTiers(): string[] {
  const src = readFileSync(join(WEB, 'e2e/routes.ts'), 'utf8')
  return [...src.matchAll(/export const ([A-Z_]+): RouteEntry\[\]/g)].map((m) => m[1])
}

function tiersSpreadBy(spec: string): string[] {
  const src = readFileSync(join(WEB, `e2e/${spec}`), 'utf8')
  const tiers = new Set(routeTiers())
  const found = new Set<string>()
  for (const m of src.matchAll(/\.\.\.([A-Z_]+)/g)) if (tiers.has(m[1])) found.add(m[1])
  return [...found]
}

const WHOLE_APP_SPECS = ['a11y.spec.ts', 'walkthrough.spec.ts']

const NARROWER_BY_DESIGN = {
  spec: 'visual.spec.ts',
  omits: ['SETTINGS_ROUTES', 'NON_NAV_ROUTES'],
  why:
    'a full-page screenshot per route per theme is a real per-route cost in baseline bytes and ' +
    'review time, unlike the axe and walkthrough legs which only cost wall-clock',
}

describe('every route tier reaches every spec that claims whole-app coverage', () => {
  it('parses the tiers and the spreads (guards against a silently-empty sweep)', () => {
    expect(routeTiers().length, 'no RouteEntry[] tiers parsed from e2e/routes.ts').toBeGreaterThanOrEqual(4)
    for (const spec of [...WHOLE_APP_SPECS, NARROWER_BY_DESIGN.spec]) {
      expect(tiersSpreadBy(spec).length, `no route tiers parsed from e2e/${spec}`).toBeGreaterThan(0)
    }
  })

  it.each(WHOLE_APP_SPECS)('%s spreads every tier', (spec) => {
    const missing = routeTiers().filter((t) => !tiersSpreadBy(spec).includes(t))
    expect(
      missing,
      `e2e/${spec} claims whole-app coverage but does not spread these tier(s) from ` +
        `e2e/routes.ts, so every route in them is exempt from that spec's contract while ` +
        `looking covered. Add the spread, or move the tier's routes somewhere that says they ` +
        `are out of scope.`,
    ).toEqual([])
  })

  it(`${NARROWER_BY_DESIGN.spec} omits exactly the tiers it declares`, () => {
    const spread = tiersSpreadBy(NARROWER_BY_DESIGN.spec)
    const actuallyOmitted = routeTiers().filter((t) => !spread.includes(t))
    expect(
      actuallyOmitted.sort(),
      `e2e/${NARROWER_BY_DESIGN.spec}'s omissions no longer match what it declares. Either it ` +
        `gained coverage (delete the entry from NARROWER_BY_DESIGN.omits) or a new tier was ` +
        `added and nobody decided whether it needs a visual baseline.\n  reason on record: ` +
        NARROWER_BY_DESIGN.why,
    ).toEqual([...NARROWER_BY_DESIGN.omits].sort())
  })
})
