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

describe('--color-on-danger', () => {
  it('is defined in BOTH themes', () => {
    const { dark, light } = blocks()
    expect(valueIn(dark, '--color-on-danger'), 'dark block must define it').toBeTruthy()
    expect(
      valueIn(light, '--color-on-danger'),
      'light must override it — its danger is a DEEP red where the dark theme\'s ink is unreadable',
    ).toBeTruthy()
  })

  it('is NOT white in dark (white on #f66c66 is 2.89:1)', () => {
    const ink = valueIn(blocks().dark, '--color-on-danger')!.toLowerCase()
    expect(ink).not.toBe('#ffffff')
    expect(ink).not.toBe('#fff')
    expect(ink).not.toBe('white')
  })

  it('IS white in light (white on #af2f29 is 6.44:1)', () => {
    expect(valueIn(blocks().light, '--color-on-danger')!.toLowerCase()).toBe('#ffffff')
  })

  it('still ships the danger fill as a per-theme hue (the reason two inks are needed)', () => {
    const { dark, light } = blocks()
    const d = valueIn(dark, '--color-danger')!.toLowerCase()
    const l = valueIn(light, '--color-danger')!.toLowerCase()
    expect(d).not.toBe(l)
  })
})

describe('components that fill with danger', () => {
  it('read the ink from the token instead of hardcoding white', () => {
    const offenders: string[] = []
    for (const rel of ['src/shared/ui/HeaderActions.tsx', 'src/shared/ui/Button.tsx']) {
      const src = readFileSync(join(WEB, rel), 'utf8')
      for (const m of src.matchAll(/danger:\s*'([^']*bg-danger[^']*)'/g)) {
        if (!/text-on-danger/.test(m[1])) offenders.push(`${rel} — ${m[1]}`)
      }
    }
    expect(
      offenders,
      'A danger-filled control must take its ink from --color-on-danger; a literal colour ' +
        'cannot be right in both themes.\n' + offenders.join('\n'),
    ).toEqual([])
  })
})
