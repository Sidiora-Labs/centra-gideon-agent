import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(import.meta.dirname, "../..")
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

const COUNTS = [
  { field: 'skills', count: 'skillCount', glyph: 'Sparkles', noun: 'skill' },
  { field: 'tools', count: 'toolCount', glyph: 'Wrench', noun: 'tool' },
  { field: 'triggers', count: 'triggerCount', glyph: 'Zap', noun: 'trigger' },
] as const

describe('every count in an agent row says what it counts', () => {
  const src = read('features/agents/AgentsListPage.tsx')

  it('reads the real native row and each actual agent count binding', () => {
    expect(src, 'the row component moved — this rail measures nothing').toMatch(/function NativeRow\(/)
    expect(src.length).toBeGreaterThan(4000)
    for (const { field, count } of COUNTS) {
      expect(src, `the ${field} count must derive from the actual agent`).toContain(
        `const ${count} = agent.${field}?.length ?? 0`,
      )
      expect(src).toContain(`${count} > 0 && <span`)
    }
  })

  it.each(COUNTS)('the $field count carries role="img" and a label naming it', ({ count, glyph, noun }) => {
    const at = src.indexOf(`${count} > 0 && <span`)
    expect(at, `the ${noun} badge was not found`).toBeGreaterThan(-1)
    const badge = src.slice(at, src.indexOf('</span>', at))
    expect(badge).toContain('role="img"')
    expect(badge).toContain('aria-label={`${' + count + '} ' + noun)
    expect(badge).toContain(`<${glyph} size={11} /> {${count}}`)
  })

  it('pluralises each named dimension using the actual displayed count', () => {
    for (const { count, noun } of COUNTS) {
      const at = src.indexOf(`${count} > 0 && <span`)
      const badge = src.slice(at, src.indexOf('</span>', at))
      expect(badge).toContain(noun + "${" + count + " === 1 ? '' : 's'}")
    }
  })

  it('no glyph+bare-count badge is left unnamed anywhere in pages/', () => {
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
      })
    const offenders: string[] = []
    for (const abs of walk(join(SRC, "features"))) {
      const text = strip(readFileSync(abs, 'utf8'))
      for (const m of text.matchAll(/<span([^>]*)>\s*<[A-Z]\w+ size=\{1[0-4]\}[^>]*\/>\s*\{([a-zA-Z_.!?]+\.length)\}\s*<\/span>/g)) {
        if (!/role="img"/.test(m[1])) {
          offenders.push(`${abs.slice(abs.indexOf('/pages/') + 7)}: {${m[2]}}`)
        }
      }
    }
    expect(
      offenders,
      'a glyph and a bare count with no noun — visible or accessible. Either state the noun in text ' +
        '(the seven-site majority) or use role="img" + aria-label (FeedbackPanel\'s dense-row form):\n  ' +
        offenders.join('\n  '),
    ).toEqual([])
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
