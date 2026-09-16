import { describe, it, expect, vi, afterEach } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { relTime } from './knowledgeMeta'


const FIXED_NOW = Date.parse('2026-09-07T12:00:00Z')
const ago = (secs: number) => new Date(FIXED_NOW - secs * 1000).toISOString()

afterEach(() => { vi.useRealTimers() })
const at = (now: number) => { vi.useFakeTimers(); vi.setSystemTime(now) }

describe('an aged library item reads as a date, not a day count', () => {
  it('shows the DATE past a week, where it used to count days forever', () => {
    at(FIXED_NOW)
    const yearOld = ago(412 * 86400)
    const out = relTime(yearOld)
    expect(out, 'a year-old item still counting days').not.toMatch(/\d+d ago/)
    expect(out).toBe(new Date(Date.parse(yearOld)).toLocaleDateString())
    expect(out).not.toBe('')
  })

  it('keeps day counts INSIDE the week — the tiers below the ceiling are unchanged', () => {
    at(FIXED_NOW)
    expect(relTime(ago(30))).toBe('just now')
    expect(relTime(ago(5 * 60))).toBe('5m ago')
    expect(relTime(ago(3 * 3600))).toBe('3h ago')
    expect(relTime(ago(2 * 86400))).toBe('2d ago')
    expect(relTime(ago(6 * 86400))).toBe('6d ago')
  })

  it('🔑 a FUTURE stamp is not "just now"', () => {
    at(FIXED_NOW)
    const ahead = new Date(FIXED_NOW + 3 * 86400 * 1000).toISOString()
    expect(relTime(ahead)).not.toBe('just now')
    expect(relTime(ahead)).toBe(new Date(Date.parse(ahead)).toLocaleDateString())
  })

  it('still renders nothing for an absent or unreadable stamp', () => {
    at(FIXED_NOW)
    for (const bad of [undefined, '', 'not a date']) expect(relTime(bad)).toBe('')
  })
})

describe('the fix matches the sibling it was measured against', () => {
  const SRC = join(process.cwd(), "src")
  const read = (...p: string[]) => readFileSync(join(SRC, ...p), 'utf8')

  it('routes parsing through the canonical parser rather than a fourth Date.parse', () => {
    const code = read('features', 'knowledge', 'knowledgeMeta.ts')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const body = code.match(/export function relTime[\s\S]*?\n\}/)?.[1 - 1] ?? ''
    expect(body, 'found the relTime body').not.toBe('')
    expect(body).toMatch(/epochSeconds\(/)
    expect(body, 'a local Date.parse is the duplication this removes').not.toMatch(/Date\.parse/)
  })

  it('VACUITY: the formatter family is still bigger than one name', () => {
    const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
    })
    const found = walk(SRC).flatMap((f) => [
      ...readFileSync(f, 'utf8').matchAll(/function (relPast|relFuture|relTime|relTimeShort|relativeTime|absTime)\s*\(/g),
    ].map((m) => m[1]))
    expect(found.length, 'time formatters across web/src').toBeGreaterThanOrEqual(9)
    expect(new Set(found).size).toBeGreaterThanOrEqual(3)
  })
})
