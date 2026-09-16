import { describe, it, expect } from 'vitest'
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
const files = () => walk(SRC).map((abs) => ({ rel: abs.slice(SRC.length + 1), src: strip(readFileSync(abs, 'utf8')) }))

describe('a declared splitter implements the splitter contract', () => {
  const claimants = () => files().filter((f) => /role="separator"/.test(f.src))

  it('finds the claimants — not vacuous', () => {
    expect(claimants().map((f) => f.rel).sort()).toEqual([
      'features/chat/ChatFilePanel.tsx',
      'features/code/CodeCockpitPage.tsx',
      'features/terminal/TerminalDrawer.tsx',
      'shared/ui/NavRail.tsx',
      'shared/ui/SidePanel.tsx',
    ])
  })

  it('each one is focusable, keyboard-operable and reports its value', () => {
    const bad: string[] = []
    for (const { rel, src } of claimants()) {
      const missing = [
        /tabIndex=\{0\}/.test(src) ? '' : 'not focusable',
        /onKeyDown/.test(src) ? '' : 'no key handler',
        /aria-valuenow/.test(src) ? '' : 'no aria-valuenow',
        /aria-valuemin/.test(src) && /aria-valuemax/.test(src) ? '' : 'no min/max',
      ].filter(Boolean)
      if (missing.length) bad.push(`${rel}: ${missing.join(', ')}`)
    }
    expect(bad, `a declared separator must be operable:\n  ${bad.join('\n  ')}`).toEqual([])
  })

  it("the name tells you it takes arrow keys — a splitter you can't guess at is unusable", () => {
    for (const { rel, src } of claimants()) {
      expect(src, `${rel} must name the interaction`).toMatch(/aria-label=\{?[`"']Resize [^`"']*arrow keys/)
    }
  })

  it('a focused splitter is visible', () => {
    for (const { rel, src } of claimants()) {
      expect(src, `${rel} needs a focus-visible seam`).toMatch(/focus-visible:bg-primary/)
    }
  })

  it('the matcher fires on the exact shape NavRail shipped', () => {
    const navRailBefore = '<div role="separator" aria-orientation="vertical" onMouseDown={() => {}} />'
    expect(/role="separator"/.test(navRailBefore)).toBe(true)
    expect(/tabIndex=\{0\}/.test(navRailBefore)).toBe(false)
    expect(/aria-valuenow/.test(navRailBefore)).toBe(false)
  })
})
