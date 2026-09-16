import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const WEB = process.cwd()
const tokens = () => readFileSync(join(WEB, 'src/shared/theme/tokens.css'), 'utf8')

function valueIn(block: string, prop: string): string | null {
  const m = block.match(new RegExp(`${prop}:\\s*([^;]+);`))
  return m ? m[1].trim() : null
}
function blocks(): { dark: string; light: string } {
  const src = tokens()
  const lightAt = src.search(/\.light\s*\{/)
  if (lightAt < 0) throw new Error('could not locate the .light block in tokens.css')
  return { dark: src.slice(0, lightAt), light: src.slice(lightAt) }
}

describe('--color-on-primary-tint', () => {
  it('is defined in BOTH themes', () => {
    const { dark, light } = blocks()
    expect(valueIn(dark, '--color-on-primary-tint'), 'the @theme default must define it').toBeTruthy()
    expect(valueIn(light, '--color-on-primary-tint'), 'light must override it — its ground is near-white').toBeTruthy()
  })

  it('the two themes carry DIFFERENT values', () => {
    const { dark, light } = blocks()
    expect(valueIn(light, '--color-on-primary-tint')).not.toBe(valueIn(dark, '--color-on-primary-tint'))
  })

  it('NEITHER mode freezes a hex — both reference a palette token', () => {
    for (const [mode, block] of Object.entries(blocks())) {
      const ink = valueIn(block, '--color-on-primary-tint')!
      expect(ink, `${mode}: a frozen hex cannot follow a scheme retint`).not.toMatch(/^#[0-9a-fA-F]{3,8}$/)
      expect(ink, `${mode}: must reference a palette token`).toMatch(/^var\(--color-[a-z-]+\)$/)
    }
  })

  it('dark takes the scheme accent shade; light takes the pale-tint ink', () => {
    const { dark, light } = blocks()
    expect(valueIn(dark, '--color-on-primary-tint')).toBe('var(--color-primary-emphasis)')
    expect(valueIn(light, '--color-on-primary-tint')).toBe('var(--color-on-primary-container)')
  })

  it('is never a color-mix — Lightning CSS mis-resolves one here', () => {
    for (const [mode, block] of Object.entries(blocks())) {
      expect(valueIn(block, '--color-on-primary-tint'), `${mode}: keep this a plain var() reference`)
        .not.toMatch(/color-mix/)
    }
  })

  it('the tonal variant actually reads the token', () => {
    const btn = readFileSync(join(WEB, 'src/shared/ui/Button.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const tonal = /tonal:\s*'([^']+)'/.exec(btn)?.[1] ?? ''
    expect(tonal, 'the tonal variant must exist').not.toBe('')
    expect(tonal, 'and carry the new ink').toContain('text-on-primary-tint')
    expect(tonal, 'not the raw primary ink that measured 4.46:1').not.toMatch(/\btext-primary\b/)
  })

  it('no OTHER component hand-rolls the failing pair', () => {
    const walk = (dir: string): string[] => {
      const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
      return readdirSync(dir).flatMap((n: string) => {
        const p = join(dir, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
      })
    }
    const offenders = walk(join(WEB, 'src'))
      .filter((f) => {
        const code = readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
        return /className="[^"]*bg-primary\/15[^"]*\btext-primary\b/.test(code)
          || /className="[^"]*\btext-primary\b[^"]*bg-primary\/15/.test(code)
      })
      .map((f) => f.slice(join(WEB, 'src').length + 1))
    expect(offenders, `these re-create the 4.46:1 pair by hand:\n${offenders.join('\n')}`).toEqual([])
  })
})
