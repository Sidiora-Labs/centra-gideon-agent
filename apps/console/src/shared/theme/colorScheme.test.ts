import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const tokens = () => readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
const decls = () => tokens().replace(/\/\*[\s\S]*?\*\//g, '')

describe('color-scheme follows the theme', () => {
  it('the light block still declares its own scheme', () => {
    const src = tokens()
    const light = src.slice(src.indexOf('.light {'), src.indexOf('color-scheme: light') + 40)
    expect(light, 'the .light block must own a color-scheme').toMatch(/color-scheme:\s*light/)
  })

  it('the dark default cannot out-rank the light block', () => {
    const src = decls()
    expect(src, 'the dark default must be scoped away from .light').toMatch(
      /:root:not\(\.light\)\s*\{\s*color-scheme:\s*dark/,
    )
    expect(
      /(^|\n):root\s*\{\s*color-scheme:\s*dark/.test(src),
      'a bare `:root { color-scheme: dark }` overrides .light on source order — that was the bug',
    ).toBe(false)
  })

  it('exactly one rule declares each scheme, so there is no second race', () => {
    const src = decls()
    expect((src.match(/color-scheme:\s*light/g) || []).length, 'one light declaration').toBe(1)
    expect((src.match(/color-scheme:\s*dark/g) || []).length, 'one dark declaration').toBe(1)
  })

  it('the light selector really is a class, which is why specificity tied', () => {
    expect(tokens()).toMatch(/(^|\n)\.light\s*\{/)
  })
})
