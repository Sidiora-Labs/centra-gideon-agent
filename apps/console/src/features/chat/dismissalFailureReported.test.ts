import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const F = (rel: string) => readFileSync(join(process.cwd(), "src/features", rel), 'utf8')
const strip = (s: string) =>
  s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const SITES: Array<[string, string, string]> = [
  ['chat/OrganizeChip.tsx', 'organizeDecline', 'decline that suggestion'],
  ['chat/RoutingChip.tsx', 'routingDismiss', 'dismiss the ${suggestion.agent} suggestion'],
  ['dashboard/DashboardLive.tsx', 'dismissDiscoverTip', 'dismiss that tip'],
  ['discover/DiscoverPage.tsx', 'dismissDiscoverTip', 'dismiss that tip'],
]

describe('a dismissal that fails says so', () => {
  it('every site uses the SHARED reporter and keeps no local copy', () => {
    for (const [rel] of SITES) {
      const src = strip(F(rel))
      expect(src, `${rel} must import the shared contract`).toMatch(
        /import \{ reportingWrite \} from '\.\.\/\.\.\/app\/shell\/reportingWrite'/,
      )
      const local = [...src.matchAll(/(function|const)\s+reportingWrite\b\s*[=(]/g)]
      expect(local.length, `${rel}: a page-local copy would shadow the shared one`).toBe(0)
    }
  })

  it('no dismissal swallows its rejection — the ratchet, keyed on the WRITES', () => {
    const offenders: string[] = []
    for (const [rel, call] of SITES) {
      const scan = strip(F(rel)).replace(/=>/g, '⇒')
      const found = [...scan.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))]
      expect(found.length, `${rel}: api.${call} must still be called`).toBeGreaterThan(0)
      for (const m of found) {
        const chain = scan.slice(m.index!, m.index! + 200)
        if (/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/.test(chain)) offenders.push(`${rel}:${call}`)
      }
    }
    expect(offenders, 'a silent dismissal fails later, where the user cannot trace it').toEqual([])
  })

  it('every dismissal routes through the reporter, and names its subject', () => {
    for (const [rel, call, what] of SITES) {
      const src = strip(F(rel))
      const at = src.indexOf(`api.${call}(`)
      const before = src.slice(Math.max(0, at - 240), at)
      expect(before, `${rel}: api.${call} must be wrapped`).toContain('reportingWrite(')
      expect(src, `${rel}: the message must name what was dismissed`).toContain(what)
    }
  })

  it('the TIPS still gate their refetch on success', () => {
    for (const rel of ['dashboard/DashboardLive.tsx', 'discover/DiscoverPage.tsx']) {
      const src = strip(F(rel))
      const at = src.indexOf('reportingWrite(')
      const after = src.slice(at, at + 320)
      expect(after, `${rel}: the guard must return`).toMatch(/\)\)\) return/)
      const guard = after.indexOf(')) return')
      const refetch = after.search(/loadDiscover\(|refresh\(/)
      expect(refetch, `${rel}: a refetch must follow`).toBeGreaterThan(-1)
      expect(guard, `${rel}: and the guard must precede it`).toBeLessThan(refetch)
    }
  })

  it('the CHIPS still hide on a failure — the opposite ruling, pinned', () => {
    const organize = strip(F('chat/OrganizeChip.tsx'))
    const at = organize.indexOf('reportingWrite(')
    const after = organize.slice(at, at + 260)
    expect(after, 'the chip clears regardless of the outcome').toContain('setProposal(null)')
    expect(after, 'so no guard may gate the hide').not.toMatch(/\)\)\) return/)

    const routing = strip(F('chat/RoutingChip.tsx'))
    const rAt = routing.indexOf('reportingWrite(')
    expect(routing.slice(rAt, rAt + 520), 'the routing chip hides too').toContain('onDismiss()')
  })

  it('the accept paths keep their own reporting — this converged onto them', () => {
    expect(F('chat/OrganizeChip.tsx')).toContain("notify(`Couldn't organize:")
    expect(F('chat/RoutingChip.tsx')).toContain("notify(`Couldn't route:")
  })
})
