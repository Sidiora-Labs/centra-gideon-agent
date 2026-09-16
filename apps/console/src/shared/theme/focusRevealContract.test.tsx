import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const REVEALS_ON_FOCUS =
  /focus-within:opacity-100|group-focus-within:opacity-100|focus-visible:opacity-100|group-focus:opacity-100/

interface Hit { file: string; line: number; cls: string; ok: boolean }

function hoverRevealed(src: string, rel: string): Hit[] {
  const out: Hit[] = []
  for (const m of src.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
    const cls = m[1] ?? m[2] ?? ''
    if (!/\bopacity-0\b/.test(cls)) continue
    if (!/group-hover(?:\/[\w-]+)?:opacity-100/.test(cls)) continue
    if (/pointer-events-none/.test(cls)) continue
    out.push({
      file: rel,
      line: src.slice(0, m.index).split('\n').length,
      cls: cls.replace(/\s+/g, ' '),
      ok: REVEALS_ON_FOCUS.test(cls),
    })
  }
  return out
}

const all = walk(SRC).flatMap((abs) => hoverRevealed(strip(readFileSync(abs, 'utf8')), abs.slice(SRC.length + 1)))

describe('the rail: hover-revealed means focus-revealed', () => {
  it('every hover-revealed focusable container also reveals on focus', () => {
    const offenders = all.filter((h) => !h.ok).map((h) => `${h.file}:${h.line}  ${h.cls.slice(0, 90)}`)
    expect(
      offenders,
      `opacity-0 does NOT remove focusability, so Tab lands on an invisible control:\n  ` +
        offenders.join('\n  '),
    ).toEqual([])
  })

  it('the rail is not vacuously green — it finds the hover-revealed containers', () => {
    expect(all.length, 'the scanner must find the tree\'s hover-revealed controls').toBeGreaterThan(20)

    const named = all.filter((h) => /group-hover\/[\w-]+:opacity-100/.test(h.cls))
    expect(named.length, 'named Tailwind groups must be scanned').toBeGreaterThan(0)

    const bad = hoverRevealed('<div className="opacity-0 group-hover:opacity-100" />', 'x.tsx')
    expect(bad.length).toBe(1)
    expect(bad[0].ok).toBe(false)
    expect(hoverRevealed('<div className="pointer-events-none opacity-0 group-hover:opacity-100" />', 'x.tsx').length).toBe(0)
  })

  it('both accepted forms count as a focus reveal', () => {
    for (const cls of [
      'opacity-0 group-hover:opacity-100 focus-within:opacity-100',
      'opacity-0 group-hover:opacity-100 focus-visible:opacity-100',
    ]) {
      expect(hoverRevealed(`<div className="${cls}" />`, 'x.tsx')[0].ok, cls).toBe(true)
    }
  })
})
