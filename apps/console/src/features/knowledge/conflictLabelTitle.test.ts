import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = readFileSync(join(process.cwd(), "src/features/knowledge/ConflictPanel.tsx"), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

describe("a claim's source label survives truncation", () => {
  it('the truncating label carries its full text in a title', () => {
    expect(CODE).toMatch(
      /<div data-type="caption" className="mt-0\.5 truncate text-on-surface-low" title=\{label\}>\{label\}<\/div>/,
    )
  })

  it('title and visible text are the same expression', () => {
    const m = /title=\{([^}]+)\}>\{([^}]+)\}<\/div>/.exec(CODE)
    expect(m, 'the pair is readable from source').toBeTruthy()
    expect(m![1].trim()).toBe(m![2].trim())
  })

  it('both sides of a conflict get it — the label is rendered once, for both claims', () => {
    const sides = [...CODE.matchAll(/<ClaimSide/g)]
    expect(sides.length, 'left and right').toBe(2)
    const defs = [...CODE.matchAll(/function ClaimSide/g)]
    expect(defs.length, 'one definition serving both').toBe(1)
  })

  it('the label still comes from the conflict, not a constant — the vacuity floor', () => {
    expect(CODE, 'left side names the item carrying the conflict')
      .toMatch(/label=\{conflict\.item_title \|\| conflict\.left_item\}/)
    expect(CODE, 'right side names the other document').toMatch(/label=\{conflict\.right_item\}/)
  })

  it('the surface still refuses to pick a winner', () => {
    expect(CODE, 'no resolve affordance').not.toMatch(/resolve|Resolve/)
    expect(CODE, 'and the undecidable case says so instead')
      .toMatch(/Both sources carry the same weight/)
  })
})
