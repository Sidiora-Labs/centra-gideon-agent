import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(import.meta.dirname, "../..")
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

const RECORDED_NOT_FIXED = new Set<string>([])

describe('the approval shield names itself', () => {
  const src = read('features/tools/ToolsPage.tsx')

  it('reads the real file (not vacuously green)', () => {
    expect(src, 'the tools page moved — this rail measures nothing').toMatch(/function RiskBadge\(/)
    expect(src, 'the approval flag must still be rendered to be asserted about').toMatch(/t\.requires_approval &&/)
    expect(src.length).toBeGreaterThan(4000)
  })

  it('the requires_approval glyph carries a name AND the role that makes it stick', () => {
    const at = src.indexOf('t.requires_approval &&')
    const badge = src.slice(at, src.indexOf('<RiskBadge', at))
    expect(badge.length, 'empty slice — vacuous').toBeGreaterThan(30)
    expect(badge, 'the glyph must be named').toMatch(/aria-label="[^"]{12,}"/)
    expect(badge, 'role="img" — a graphic whose label is its only text').toMatch(/role="img"/)
  })

  it('the name says what happens, not what the field is called', () => {
    const at = src.indexOf('t.requires_approval &&')
    const badge = src.slice(at, src.indexOf('<RiskBadge', at))
    const label = /aria-label="([^"]+)"/.exec(badge)?.[1] ?? ''
    expect(label.toLowerCase(), `"${label}" should describe asking, not the flag name`).toMatch(/ask/)
    expect(label, 'and must not leak the snake_case field name').not.toMatch(/requires_approval/)
  })

  it('RiskBadge still states its own dimension in visible text', () => {
    const at = src.indexOf('function RiskBadge(')
    expect(at, 'RiskBadge moved — this rail measures nothing').toBeGreaterThan(-1)
    const badge = src.slice(at, src.indexOf('\n}', at))
    expect(badge.length, 'empty slice — vacuous').toBeGreaterThan(80)
    expect(badge, 'the risk tier is visible text, not a glyph').toMatch(/\{label\}\s*<\/span>/)
    expect(badge, 'and it names its dimension on hover').toMatch(/title=\{`Risk: \$\{label\}`\}/)
  })

  it('no informational warn/danger glyph in pages/ is left unnamed beside a named sibling', () => {
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
      })
    const offenders: string[] = []
    for (const abs of walk(join(SRC, "features"))) {
      const text = strip(readFileSync(abs, 'utf8'))
      for (const m of text.matchAll(/<(Shield\w+|AlertTriangle|TriangleAlert)\b([^>]*)\/>/g)) {
        const attrs = m[2]
        if (!/text-(warn|danger)|--color-(warn|danger)/.test(attrs)) continue
        if (/aria-label=/.test(attrs) || /\baria-hidden\b/.test(attrs)) continue
        const after = text.slice(m.index! + m[0].length, m.index! + m[0].length + 160)
        if (/^[\s}:)]*(<[a-zA-Z]|\{)/.test(after)) continue
        const rel = abs.slice(abs.indexOf('/pages/') + 7)
        if (RECORDED_NOT_FIXED.has(`${rel}: <${m[1]}>`)) continue
        offenders.push(`${rel}: <${m[1]}>`)
      }
    }
    expect(
      offenders,
      'these warn/danger glyphs carry a fact with no accessible name and no adjacent sentence. ' +
        'Name them (aria-label + role="img") or mark them aria-hidden if the text beside them says it:\n  ' +
        offenders.join('\n  '),
    ).toEqual([])
  })

  it('every recorded-not-fixed exemption still exists and is still unnamed', () => {
    for (const entry of RECORDED_NOT_FIXED) {
      const [rel, glyph] = entry.split(': ')
      const text = strip(readFileSync(join(SRC, "features", rel), 'utf8'))
      const tag = glyph.replace(/[<>]/g, '')
      const found = [...text.matchAll(new RegExp(`<${tag}\\b([^>]*)\\/>`, 'g'))]
        .some((m) => /text-(warn|danger)|--color-(warn|danger)/.test(m[1]) && !/aria-label=|\baria-hidden\b/.test(m[1]))
      expect(found, `${entry} is exempted but no longer matches — drop the exemption`).toBe(true)
    }
    expect(RECORDED_NOT_FIXED.size, 'the exemption list should shrink, never grow').toBe(0)
  })

  it('the pages sweep reads a real tree (vacuity floor)', () => {
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
      })
    expect(walk(join(SRC, "features")).length, 'the pages sweep found nothing').toBeGreaterThan(60)
  })
})
