import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

function code(abs: string): string {
  return readFileSync(abs, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/.*$/gm, '$1')
}

function panelSlice(): string {
  const src = code(join(SRC, 'features/apps/AppsSection.tsx'))
  const start = src.indexOf('function StoreDetailPanel')
  expect(start, 'StoreDetailPanel renamed — re-point this rail').toBeGreaterThan(-1)
  const next = src.indexOf('\nfunction ', start + 1)
  return src.slice(start, next === -1 ? undefined : next)
}

describe('StoreDetailPanel banner (the card contract, both art paths)', () => {
  it('renders the banner unconditionally with the two-path art seam', () => {
    const slice = panelSlice()
    expect(slice, 'the data-art seam is gone — the banner lost its two-path contract')
      .toContain("data-art={item.heroUrl ? 'hero' : 'generated'}")
    expect(slice, 'the generated-art fallback is gone — hero-less apps drop the banner again')
      .toContain('artGradient(item.name)')
  })

  it('does not gate the banner block behind heroUrl', () => {
    const slice = panelSlice()
    const bannerAt = slice.indexOf('data-art=')
    const gate = slice.lastIndexOf('{item.heroUrl && (', bannerAt)
    const divStart = slice.lastIndexOf('<div', bannerAt)
    expect(
      gate === -1 || gate < slice.lastIndexOf('return (', bannerAt) || gate < divStart - 200,
      'the banner div is gated behind item.heroUrl again',
    ).toBe(true)
    expect(divStart, 'no banner div found near the data-art seam').toBeGreaterThan(-1)
  })
})
