import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { SCHEMES } from '../../shared/theme/schemes'

type Mode = 'dark' | 'light'

const read = (path: string) => readFileSync(join(process.cwd(), path), 'utf8')
const css = read('src/shared/theme/tokens.css')
const component = read('src/features/chat/SessionMarkerRail.tsx')
const modes = ['dark', 'light'] as const

function token(name: string, mode: Mode): string {
  const scope = mode === 'dark'
    ? css.slice(0, css.search(/\.light\s*\{/))
    : /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
  const value = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`).exec(scope)?.[1]
  if (!value) throw new Error(`could not find ${mode} ${name}`)
  return value
}

function contrast(a: string, b: string): number {
  const luminance = (hex: string) => {
    const channels = [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16) / 255)
      .map((channel) => channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
  }
  const [low, high] = [luminance(a), luminance(b)].sort((x, y) => x - y)
  return (high + 0.05) / (low + 0.05)
}

describe('Session map marks remain visible on their rail track', () => {
  const expression = [...component.matchAll(/current === index \? '([^']+)' : '([^']+)'/g)]
    .find((match) => match[1].includes('bg-') && match[2].includes('bg-'))
  const tones = expression?.slice(1).map((branch) => /\bbg-([a-z-]+)/.exec(branch)?.[1]) ?? []

  it('reads the rail track and both mark tones from production declarations', () => {
    expect(component).toContain('bg-rail')
    expect(tones).toHaveLength(2)
    expect(new Set(tones).size).toBe(2)
    expect(SCHEMES.length).toBeGreaterThanOrEqual(12)
  })

  it('keeps every scheme current mark and the history ink at 3:1 in both modes', () => {
    const [currentTone, historyTone] = tones
    for (const mode of modes) {
      const track = token('--color-rail', mode)
      const history = token(`--color-${historyTone}`, mode)
      expect(contrast(history, track), `${mode} history ${historyTone} on rail`).toBeGreaterThanOrEqual(3)
      for (const scheme of SCHEMES) {
        const current = scheme.colors[`--color-${currentTone}`]?.[mode] ?? token(`--color-${currentTone}`, mode)
        expect(contrast(current, track), `${scheme.id}/${mode} ${currentTone} on rail`).toBeGreaterThanOrEqual(3)
      }
    }
  })
})
