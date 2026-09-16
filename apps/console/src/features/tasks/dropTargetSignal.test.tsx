import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const BOARD = join(process.cwd(), "src/features/tasks/TaskBoard.tsx")
const code = () => readFileSync(BOARD, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the drop target is signalled by more than a background tint', () => {
  it('the over state draws a dashed outline in the column tone', () => {
    expect(code(), 'the drop target needs a non-color-only edge')
      .toMatch(/outline: isOver \? `1\.5px dashed \$\{s\.tone\}` : '1\.5px solid transparent'/)
  })

  it('and it still carries the tint and the scale, so the three signals stay three', () => {
    const src = code()
    const tint = src.match(/isOver \? `color-mix\(in srgb, \$\{s\.tone\} 12%, var\(--color-surface-container\)\)`/g) ?? []
    expect(tint.length, 'one shared drop style owns the tint').toBe(1)
    expect(src.match(/style=\{dropStyle\}/g), 'both collapsed and expanded columns apply that style').toHaveLength(2)
    expect(src, 'the lift that makes the target feel picked out').toMatch(/animate=\{\{ scale: isOver \? 1 \+ expr\(/)
  })

  it('the signal is bound to a live drag, not to hover', () => {
    expect(code()).toMatch(/const isOver = overCol === s\.key && dragId != null/)
  })

  it('the matcher would fail if the outline became color-only', () => {
    const re = /outline: isOver \? `1\.5px dashed \$\{s\.tone\}` : '1\.5px solid transparent'/
    expect(re.test("outline: isOver ? `1.5px dashed ${s.tone}` : '1.5px solid transparent'")).toBe(true)
    expect(re.test("outline: 'none'")).toBe(false)
    expect(re.test("background: isOver ? `color-mix(in srgb, ${s.tone} 12%, …)`")).toBe(false)
  })
})


describe('the picked-up card is legible, and stays tokenised', () => {
  const src = code

  it('all four signals are bound to `dragging`', () => {
    const s = src()
    expect(s, 'the dim').toMatch(/opacity: dragging \? 0\.5 : 1,/)
    expect(s, 'the scale').toMatch(/scale: dragging \? 1 \+ expr\(/)
    expect(s, 'the tilt').toMatch(/rotate: dragging \? -expr\(/)
    expect(s, 'the lift').toMatch(/boxShadow: dragging \? 'var\(--shadow-lift\)' : 'var\(--shadow-rest\)',/)
  })

  it('the lift stays a TOKEN, so this surface never joins the scheme-blind shadow family', () => {
    const s = src()
    expect(s).toMatch(/var\(--shadow-lift\)/)
    expect(/shadow-2xl/.test(s), 'the board must not adopt the scheme-blind shadow').toBe(false)
  })

  it('only the card being dragged lifts', () => {
    expect(src()).toMatch(/dragging=\{dragId === t\.id\}/)
  })

  it('the matcher fails on a dim-only version', () => {
    const dimOnly = 'opacity: dragging ? 0.5 : 1,'
    expect(/boxShadow: dragging \? 'var\(--shadow-lift\)'/.test(dimOnly)).toBe(false)
    expect(/opacity: dragging \? 0\.5 : 1,/.test(dimOnly)).toBe(true)
  })
})
