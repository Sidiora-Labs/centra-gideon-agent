import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

function metaLine(): string {
  const src = read('features/tasks/TasksListPage.tsx')
  const at = src.indexOf('function MetaLine(')
  expect(at, 'MetaLine must still exist').toBeGreaterThan(-1)
  const end = src.indexOf('\nfunction ', at + 1)
  expect(end, 'MetaLine must terminate before the next top-level function').toBeGreaterThan(at)
  return src
    .slice(at, end)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
}

describe('the tasks meta line separates by gap, not by a glyph', () => {
  it('reads the real meta line (vacuity floor)', () => {
    const meta = metaLine()
    expect(meta.length, 'the slice is empty — every assertion below is vacuous').toBeGreaterThan(400)
    expect(meta, 'expected the identity group').toMatch(/const lead\b/)
    expect(meta, 'expected the schedule group').toMatch(/const tail\b/)
    expect(meta, 'expected the exit-criteria item').toMatch(/criteria/)
  })

  it('carries no separator glyph at all', () => {
    const meta = metaLine()
    const glyphs = ['·', '•', '–'].filter((g) => meta.includes(g))
    expect(
      glyphs.map((g) => `U+${g.codePointAt(0)!.toString(16).toUpperCase()}`),
      'a separator glyph is back in a flex-wrap meta line. It strands at the start of the wrapped ' +
        'line — 4 of 4 rows did at 320px, the width SC 1.4.10 requires. Separate by `gap-x-*`.',
    ).toEqual([])
  })

  it('and the PRESENCE guard that could not see line position is gone', () => {
    expect(metaLine(), 'the presence-guarded separator must not come back')
      .not.toMatch(/lead\.length > 0 \|\| i > 0/)
  })

  it('still separates its items — the gap is not zero', () => {
    expect(metaLine(), 'the meta line must keep a horizontal gap').toMatch(/gap-x-m\b/)
    expect(metaLine(), 'and it must still be the wrapping container the measurement assumed')
      .toMatch(/flex-wrap/)
  })

  it('the 320px measurement stays written down, because no swept tier reproduces it', () => {
    const src = read('features/tasks/TasksListPage.tsx')
    expect(src, 'the reflow width must be named').toMatch(/320px/)
    expect(src, 'and the criterion that makes it in scope').toMatch(/1\.4\.10|Reflow/)
  })

  it('#2224 is no longer described as having a conditional to converge onto', () => {
    // The rewritten sibling states the current gap policy directly instead of narrating the
    // removed conditional. Keep the reason and check the real metadata container as well.
    const prompts = read('features/prompts/PromptsListPage.tsx')
    expect(prompts, "the pinned phrase #2224's rail requires must survive")
      .toMatch(/tests PRESENCE, not line/)
    expect(prompts, 'the sibling documents its current gap policy')
      .toMatch(/wrapping metadata uses gaps/)
    const metadata = prompts.match(/<div data-type="body-s" className="[^"]*flex-wrap[^"]*">[\s\S]*?<\/div>/)?.[0] ?? ''
    expect(metadata, 'found the sibling wrapping metadata container').not.toBe('')
    expect(metadata, 'the stated gap policy is implemented').toMatch(/gap-x-m\b/)
    expect(metadata, 'the sibling has no content separator to strand').not.toMatch(/[·•–]/)
    expect(metadata, 'the removed presence guard is still absent').not.toMatch(/lead\.length > 0 \|\| i > 0/)
  })
})
