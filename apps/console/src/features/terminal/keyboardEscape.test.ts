import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { escapeGate, ESC_ESC_MS } from './escapeGate'


describe('escapeGate — which keydown releases focus', () => {
  it('forwards a lone Escape to the shell (vim, readline)', () => {
    const d = escapeGate('Escape', 1000, 0)
    expect(d.forward).toBe(true)
    expect(d.release).toBe(false)
    expect(d.lastEscAt).toBe(1000)
  })

  it('releases on a second Escape inside the window, and swallows it', () => {
    const first = escapeGate('Escape', 1000, 0)
    const second = escapeGate('Escape', 1000 + ESC_ESC_MS - 1, first.lastEscAt)
    expect(second.release).toBe(true)
    expect(second.forward).toBe(false)
    expect(second.lastEscAt).toBe(0)
  })

  it('forwards BOTH when the two Escapes are far apart', () => {
    const first = escapeGate('Escape', 1000, 0)
    const later = escapeGate('Escape', 1000 + ESC_ESC_MS, first.lastEscAt)
    expect(later.release).toBe(false)
    expect(later.forward).toBe(true)
    expect(escapeGate('Escape', 1000 + ESC_ESC_MS + 10, later.lastEscAt).release).toBe(true)
  })

  it('a key between two Escapes disarms the release', () => {
    const first = escapeGate('Escape', 1000, 0)
    const k = escapeGate('k', 1010, first.lastEscAt)
    expect(k.forward).toBe(true)
    expect(k.lastEscAt).toBe(0)
    expect(escapeGate('Escape', 1020, k.lastEscAt).release).toBe(false)
  })

  it('never releases on Tab — completion still belongs to the shell', () => {
    for (const key of ['Tab', 'Enter', 'ArrowUp', 'c']) {
      const d = escapeGate(key, 500, 400)
      expect(d.forward, `${key} must reach the PTY`).toBe(true)
      expect(d.release, `${key} must not release focus`).toBe(false)
    }
  })
})

describe('TerminalView wires the escape to the DOM', () => {
  const src = readFileSync(join(process.cwd(), "src/features/terminal/TerminalView.tsx"), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('hands the key decision to escapeGate rather than re-deriving it', () => {
    expect(src).toMatch(/attachCustomKeyEventHandler/)
    expect(src).toMatch(/escapeGate\(e\.key, e\.timeStamp, lastEsc\)/)
  })

  it('takes xterm out of the tab order so Tab can pass the terminal by', () => {
    expect(src).toMatch(/\.xterm-helper-textarea/)
    expect(src).toMatch(/setAttribute\('tabindex', '-1'\)/)
  })

  it('gives the terminal ONE labelled tab stop that Enter steps into', () => {
    expect(src).toMatch(/tabIndex=\{0\}/)
    expect(src).toMatch(/role="group"/)
    expect(src).toMatch(/aria-label="Terminal session"/)
    expect(src).toMatch(/e\.key === 'Enter' && e\.target === e\.currentTarget/)
  })

  it('rings on plain :focus, because the release focuses it programmatically', () => {
    expect(src).toMatch(/focus:ring-2 focus:ring-inset focus:ring-primary\b/)
    expect(src).not.toMatch(/focus-visible:ring/)
  })

  it('advises the way out, which is what 2.1.2 actually requires', () => {
    expect(src).toMatch(/Enter to type here · Esc Esc to leave/)
    expect(src.match(/aria-describedby/g) ?? [], 'both the group and the PTY point at the hint').toHaveLength(2)
    expect(src).not.toMatch(/aria-hidden=\{hintRead\}/)
  })
})
