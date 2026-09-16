import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(import.meta.dirname, "../..")
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

function promptsMetaLine(): string {
  const src = strip(readFileSync(join(SRC, 'features/prompts/PromptsListPage.tsx'), 'utf8'))
  const at = src.indexOf('flex flex-wrap items-center gap-x-m')
  expect(at, "the prompts meta line moved — this rail measures nothing").toBeGreaterThan(-1)
  return src.slice(at, src.indexOf('</div>', at))
}

describe('the prompts meta line separates by gap, not by a glyph', () => {
  it('reads the real meta line (vacuity floor)', () => {
    const meta = promptsMetaLine()
    expect(meta.length, 'the slice is empty — every assertion below is vacuous').toBeGreaterThan(80)
    expect(meta, 'expected the source label').toMatch(/sourceLabel\(/)
    expect(meta, 'expected the var count').toMatch(/vars\.length/)
    expect(meta, 'expected the description').toMatch(/r\.description/)
  })

  it('carries no separator glyph at all', () => {
    const meta = promptsMetaLine()
    const glyphs = ['·', '•', '–'].filter((g) => meta.includes(g))
    expect(
      glyphs.map((g) => `U+${g.codePointAt(0)!.toString(16).toUpperCase()}`),
      'a separator glyph is back in a flex-wrap meta line. It will strand at the start of the ' +
        'wrapped line — 39 of 39 rows did, at 834px and 390px. Separate by `gap-x-*`.',
    ).toEqual([])
  })

  it('still separates its items — the gap is not zero', () => {
    expect(promptsMetaLine(), 'the meta line must keep a horizontal gap').toMatch(/gap-x-m\b/)
  })

  it('the sibling it would otherwise be converged onto is recorded as insufficient', () => {
    const src = readFileSync(join(SRC, 'features/prompts/PromptsListPage.tsx'), 'utf8')
    expect(
      src,
      'the note explaining why the canonical conditional does not fix wrapping must stay',
    ).toMatch(/PRESENCE, not line\s*\n?\s*\*?\s*position|tests PRESENCE, not line/)
  })
})
