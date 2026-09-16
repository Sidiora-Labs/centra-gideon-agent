import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n) ? [p] : []
  })

describe('unavailableWhen — the raw-button carrier', () => {
  const src = read('shared/ui/unavailable.ts')

  it('keeps the tab stop instead of going natively disabled', () => {
    const returned = src.slice(src.lastIndexOf('return {'))
    expect(returned, 'the missing-input branch returns aria-disabled').toMatch(/'aria-disabled': true,/)
    expect(returned, 'and never the native attribute').not.toMatch(/\bdisabled: true/)
    expect(src, 'and it refuses the click itself').toMatch(/onClickCapture/)
    expect(src, 'the reason rides in the title').toMatch(/title: \[opts\?\.title, reason\]\.filter\(Boolean\)\.join\(' — '\)/)
  })

  it('goes natively disabled while busy AND announces it', () => {
    expect(src).toMatch(/if \(opts\?\.busy\) return \{ disabled: true, 'aria-busy': true/)
    expect(src, 'the file explains why native disabled is kept here').toMatch(/double-fire/)
  })

  it('returns nothing when nothing is missing', () => {
    expect(src).toMatch(/if \(!missing\) return opts\?\.title \? \{ title: opts\.title \} : \{\}/)
  })

  it('is actually used, and by raw buttons', () => {
    const users = walk(SRC).filter((f) => /\bunavailableWhen\(/.test(readFileSync(f, 'utf8')))
    expect(users.length, 'adopting files').toBeGreaterThanOrEqual(10)
    const anyRaw = users.some((f) => /<button[\s\S]{0,400}?unavailableWhen\(/.test(readFileSync(f, 'utf8')))
    expect(anyRaw, 'at least one is the raw-button case it exists for').toBe(true)
  })
})

describe('disabledReason — the Button carrier', () => {
  it('Button still accepts and renders it', () => {
    const btn = read('shared/ui/Button.tsx')
    expect(btn, 'the prop exists').toMatch(/disabledReason\??:/)
    expect(btn, 'aria-disabled, not native disabled').toMatch(/aria-disabled/)
  })

  it('and the two carriers agree about what soft-off means', () => {
    for (const rel of ['shared/ui/unavailable.ts', 'shared/ui/Button.tsx']) {
      expect(read(rel), `${rel} emits aria-disabled`).toMatch(/aria-disabled/)
    }
  })
})
