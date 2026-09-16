import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { sessionRecencyMs } from './epoch'


describe('sessionRecencyMs survives the payload the API actually sends', () => {
  it('ignores an empty-string last_ts instead of returning NaN', () => {
    const s = { last_activity_ts: '2026-08-12T10:00:00Z', last_ts: '' }
    expect(Number.isNaN(sessionRecencyMs(s))).toBe(false)
    expect(sessionRecencyMs(s)).toBe(Date.parse('2026-08-12T10:00:00Z'))
  })

  it('falls through an empty string to the next field', () => {
    const s = { last_activity_ts: '', last_ts: '2026-08-11T09:00:00Z' }
    expect(sessionRecencyMs(s)).toBe(Date.parse('2026-08-11T09:00:00Z'))
  })

  it('falls all the way to created', () => {
    const s = { last_activity_ts: '', last_ts: '', created: '2026-08-01T00:00:00Z' }
    expect(sessionRecencyMs(s)).toBe(Date.parse('2026-08-01T00:00:00Z'))
  })

  it('returns 0 — never NaN — when nothing is usable', () => {
    expect(sessionRecencyMs({})).toBe(0)
    expect(sessionRecencyMs({ last_activity_ts: '', last_ts: '', created: '' })).toBe(0)
    expect(sessionRecencyMs({ last_activity_ts: 'not a date' })).toBe(0)
  })

  it('the OLD expression really did produce NaN on that input — the bug was reachable', () => {
    const s = { last_activity_ts: undefined as string | undefined, last_ts: '' }
    const old = new Date(s.last_activity_ts ?? s.last_ts ?? 0).getTime()
    expect(Number.isNaN(old), 'the `??` chain passes the empty string straight into Date').toBe(true)
    expect(Number.isNaN(sessionRecencyMs(s))).toBe(false)
  })

  it('sorts a mixed list the way a user expects, with no NaN row jumping', () => {
    const rows = [
      { key: 'oldest', last_activity_ts: '', last_ts: '', created: '2026-01-01T00:00:00Z' },
      { key: 'newest', last_activity_ts: '2026-08-12T12:00:00Z', last_ts: '' },
      { key: 'unknown', last_activity_ts: '', last_ts: '' },
      { key: 'middle', last_activity_ts: '', last_ts: '2026-05-05T05:05:05Z' },
    ]
    const order = [...rows].sort((a, b) => sessionRecencyMs(b) - sessionRecencyMs(a)).map((r) => r.key)
    expect(order).toEqual(['newest', 'middle', 'oldest', 'unknown'])
  })
})

describe('all three call sites share it now', () => {
  const SRC = join(process.cwd(), "src")
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  it('the dashboard no longer builds a Date from a `??` chain', () => {
    const src = read('features/dashboard/DashboardPage.tsx')
    expect(src).toContain('.sort((a, b) => sessionRecencyMs(b) - sessionRecencyMs(a))')
    expect(src, 'the breakable spelling must be gone').not.toMatch(/new Date\([^)]*\?\?[^)]*\)\.getTime\(\)/)
  })

  it('both chat sites use the helper too, so there is one spelling left', () => {
    const src = read('features/ChatPage.tsx')
    expect(src).toContain('.sort((a, b) => sessionRecencyMs(b) - sessionRecencyMs(a))')
    expect(src).toContain('const recency = sessionRecencyMs')
    expect(src, 'no hand-rolled Date.parse fallback chain should remain')
      .not.toMatch(/Date\.parse\(\w+\.last_activity_ts \|\| /)
  })

  it('no `new Date(x ?? y).getTime()` survives anywhere in pages/', () => {
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
      })
    const offenders = walk(join(SRC, "features"))
      .filter((abs) => /new Date\([^)]*\?\?[^)]*\)\.getTime\(\)/.test(readFileSync(abs, 'utf8')))
    expect(offenders).toEqual([])
  })

  it('the helper lives with the parser that already handles the empty string', () => {
    const epoch = read('shared/data/epoch.ts')
    expect(epoch).toMatch(/export function sessionRecencyMs\b/)
    expect(epoch, 'it must reuse epochSeconds rather than re-parsing').toMatch(/epochSeconds\(s\.last_activity_ts\)/)
  })
})
