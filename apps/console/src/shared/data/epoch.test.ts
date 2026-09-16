import { describe, it, expect, vi, afterEach } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { epochSeconds, sessionActivitySeconds, sessionRecencyMs } from './epoch'
import { relPast, relFuture, absTime } from '../../features/schedule/scheduleMeta'


describe('epochSeconds', () => {
  it('passes a number through as seconds', () => {
    expect(epochSeconds(1786521600)).toBe(1786521600)
  })

  it('parses the ISO-8601 the history endpoint actually sends, microseconds and all', () => {
    expect(epochSeconds('2026-08-12T08:00:00.006315+00:00')).toBeCloseTo(1786521600.006, 2)
  })

  it('returns undefined for everything unreadable', () => {
    for (const v of [undefined, null, '', 'not a date', NaN, Infinity]) {
      expect(epochSeconds(v as number | string | null | undefined), `${String(v)} must not become a number`).toBeUndefined()
    }
  })

  it('does not treat 0 as missing', () => {
    expect(epochSeconds(0)).toBe(0)
  })
})

describe('the two session shapes agree about their timestamps', () => {
  const api = readFileSync(join(process.cwd(), "src/shared/data/api.ts"), 'utf8')

  function declared(name: string, field: string): string | null {
    const i = api.indexOf(`export interface ${name} {`)
    if (i < 0) return null
    const body = api.slice(i, api.indexOf('\n}', i))
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const m = body.match(new RegExp(`\\b${field}\\??:\\s*([^;\n]+)`))
    return m ? m[1].trim() : null
  }

  it('finds both interfaces (not vacuously green)', () => {
    expect(declared('ChatSession', 'messages'), 'ChatSession must be parseable').toBeTruthy()
    expect(declared('ChatSessionSummary', 'messages'), 'ChatSessionSummary must be parseable').toBeTruthy()
  })

  it('last_ts is a string in BOTH, because that is what the endpoints send', () => {
    expect(declared('ChatSession', 'last_ts')).toBe('string')
    expect(declared('ChatSessionSummary', 'last_ts')).toBe('string')
  })

  it('no timestamp field contradicts itself across the two shapes', () => {
    const fields = ['last_ts', 'last_activity_ts', 'created', 'last_message']
    const clashes: string[] = []
    for (const f of fields) {
      const a = declared('ChatSession', f)
      const b = declared('ChatSessionSummary', f)
      if (a && b && a !== b) clashes.push(`${f}: ChatSession=${a} vs ChatSessionSummary=${b}`)
    }
    expect(clashes, `one entity, two shapes, disagreeing:\n${clashes.join('\n')}`).toEqual([])
  })
})

describe('sessionActivitySeconds — the field choice, in one place', () => {
  const ISO = '2026-08-07T04:24:20.670204+00:00'

  it('prefers last_activity_ts, then last_ts, then created', () => {
    expect(sessionActivitySeconds({ last_activity_ts: ISO, last_ts: '2020-01-01T00:00:00Z', created: '2019-01-01T00:00:00Z' }))
      .toBeCloseTo(Date.parse(ISO) / 1000, 3)
    expect(sessionActivitySeconds({ last_ts: ISO, created: '2019-01-01T00:00:00Z' })).toBeCloseTo(Date.parse(ISO) / 1000, 3)
    expect(sessionActivitySeconds({ created: ISO })).toBeCloseTo(Date.parse(ISO) / 1000, 3)
  })

  it('skips the EMPTY STRING the endpoint really sends for last_ts', () => {
    expect(sessionActivitySeconds({ last_activity_ts: '', last_ts: '', created: ISO }))
      .toBeCloseTo(Date.parse(ISO) / 1000, 3)
  })

  it('is undefined — not 0 — when nothing reads', () => {
    expect(sessionActivitySeconds({})).toBeUndefined()
    expect(sessionActivitySeconds({ last_activity_ts: 'not a date', last_ts: '', created: '' })).toBeUndefined()
    expect(sessionRecencyMs({})).toBe(0)
  })

  it('the sorter and the label now read the SAME field choice', () => {
    const s = { last_activity_ts: ISO, last_ts: '', created: '2019-01-01T00:00:00Z' }
    expect(sessionRecencyMs(s)).toBeCloseTo((sessionActivitySeconds(s) as number) * 1000, 0)
  })

  it('#/chat reads both through lib/epoch, with no local parse left', () => {
    const src = readFileSync(join(process.cwd(), "src/features/ChatPage.tsx"), 'utf8')
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code, 'the label takes the shared field choice').toMatch(/relTimeShort\(sessionActivitySeconds\(s\)\)/)
    expect(code, 'the formatter parses through epochSeconds').toMatch(/const at_s = epochSeconds\(at\)/)
    expect(code, 'no hand-rolled fallback chain remains').not.toMatch(/last_activity_ts \|\| s\.last_ts/)
    expect(code, 'no local Date.parse in the formatter').not.toMatch(/function relTimeShort[\s\S]{0,300}?Date\.parse/)
  })
})

describe('the formatters render an empty form, never NaN', () => {
  afterEach(() => vi.useRealTimers())

  it('reads an ISO string as a past time', () => {
    vi.useFakeTimers().setSystemTime(new Date('2026-08-12T12:00:00Z'))
    expect(relPast('2026-08-12T08:00:00+00:00')).toBe('4h ago')
    expect(relPast('2026-08-12T08:00:00.006315+00:00')).toBe('3h ago')
  })

  it('says nothing rather than NaN for garbage', () => {
    expect(relFuture('not a date')).toBe('')
    expect(absTime('not a date')).toBe('')
    expect(relPast('not a date')).toBe('never')
  })

  it('never emits the string NaN for any input a wire field could hold', () => {
    const inputs = [undefined, null, '', 'not a date', NaN, '2026-08-12T08:00:00Z', 1786521600, 0, '1786521600']
    for (const v of inputs) {
      for (const [name, f] of [['relPast', relPast], ['relFuture', relFuture], ['absTime', absTime]] as const) {
        expect(f(v as number | string | null | undefined), `${name}(${String(v)})`).not.toMatch(/NaN|Invalid/)
      }
    }
  })
})

describe('every relative-time formatter in the tree coerces', () => {
  const SRC = join(process.cwd(), "src")
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
    })

  const formatters = walk(SRC).flatMap((f) => {
    const src = readFileSync(f, 'utf8')
    const out: Array<{ file: string; name: string; body: string }> = []
    for (const m of src.matchAll(/function (relPast|relFuture|relTime|relTimeShort|relativeTime|absTime)\s*\(/g)) {
      const open = src.indexOf('{', m.index! + m[0].length)
      if (open < 0) continue
      let depth = 0
      for (let i = open; i < src.length; i++) {
        if (src[i] === '{') depth++
        else if (src[i] === '}') { depth--; if (depth === 0) { out.push({ file: f.replace(SRC, 'src'), name: m[1], body: src.slice(open, i + 1) }); break } }
      }
    }
    return out
  })

  it('finds them (not vacuously green)', () => {
    expect(formatters.map((f) => `${f.file}:${f.name}`).sort()).toContain('src/features/schedule/scheduleMeta.ts:relPast')
    expect(formatters.length, 'the matcher must find the time formatters').toBeGreaterThanOrEqual(9)
  })

  it('has none that does arithmetic on unvalidated input', () => {
    const bare = formatters
      .filter((f) => !/epochSeconds\(/.test(f.body) && !/Number\.isNaN\(/.test(f.body))
    expect(bare.map((f) => `${f.file}:${f.name}`), 'a formatter reads a timestamp without validating it').toEqual([])
  })
})
