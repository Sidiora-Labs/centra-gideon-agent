import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { renderHook, act } from '@testing-library/react'
import { useResizablePanel } from './useResizablePanel'


const KEY = 'fit-test'
const W_KEY = `${KEY}-w`

function setViewport(w: number) {
  Object.defineProperty(window, 'innerWidth', { value: w, writable: true, configurable: true })
  act(() => { window.dispatchEvent(new Event('resize')) })
}

describe('useResizablePanel keeps the rendered panel inside the viewport', () => {
  beforeEach(() => { localStorage.clear() })
  afterEach(() => { vi.restoreAllMocks() })

  const mount = (def = 480) =>
    renderHook(() => useResizablePanel(KEY, { def, min: 360, max: 900, side: 'right' }))

  it('on a wide viewport fitWidth IS the stored width — the clamp does not engage', () => {
    setViewport(1600)
    localStorage.setItem(W_KEY, '720')
    const { result } = mount()
    expect(result.current.width).toBe(720)
    expect(result.current.fitWidth, 'nothing to clamp at 1600px').toBe(720)
  })

  it('a stored width wider than the viewport renders clamped, leaving an edge sliver', () => {
    setViewport(700)
    localStorage.setItem(W_KEY, '900')
    const { result } = mount()
    expect(result.current.fitWidth).toBe(700 - 32)
    expect(result.current.fitWidth, 'the far edge must stay reachable').toBeLessThan(700)
  })

  it('and the STORED width is NOT overwritten — a re-widen restores the choice', () => {
    setViewport(700)
    localStorage.setItem(W_KEY, '900')
    const { result } = mount()
    expect(result.current.width, 'the user still chose 900').toBe(900)
    expect(localStorage.getItem(W_KEY), 'and it must still be on disk').toBe('900')
    setViewport(1600)
    expect(result.current.fitWidth, 'a wide screen gives the choice back').toBe(900)
  })

  it('it follows a resize in BOTH directions, not just at mount', () => {
    setViewport(1600)
    localStorage.setItem(W_KEY, '800')
    const { result } = mount()
    expect(result.current.fitWidth).toBe(800)
    setViewport(600)
    expect(result.current.fitWidth).toBe(600 - 32)
    setViewport(1600)
    expect(result.current.fitWidth).toBe(800)
  })

  it('a bottom-docked panel clamps against innerHeight, not innerWidth', () => {
    Object.defineProperty(window, 'innerWidth', { value: 1600, writable: true, configurable: true })
    Object.defineProperty(window, 'innerHeight', { value: 500, writable: true, configurable: true })
    localStorage.setItem('drawer-w', '900')
    const { result } = renderHook(() =>
      useResizablePanel('drawer', { def: 300, min: 100, max: 900, side: 'bottom' }))
    expect(result.current.fitWidth, 'clamped by height, not by the wide viewport').toBe(500 - 32)
  })

  it('edgePeek is configurable, and 0 means the panel may fill the viewport', () => {
    setViewport(700)
    localStorage.setItem('np-w', '900')
    const { result } = renderHook(() =>
      useResizablePanel('np', { def: 480, min: 360, max: 900, side: 'right', edgePeek: 0 }))
    expect(result.current.fitWidth).toBe(700)
  })
})

describe('the two right-docked panels both render the clamped width', () => {
  const strip = (s: string) =>
    s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
  const read = (rel: string) => strip(readFileSync(join(process.cwd(), "src", rel), 'utf8'))

  it('ChatFilePanel renders fitWidth and reports the stored width to aria', () => {
    const src = read('features/chat/ChatFilePanel.tsx')
    expect(src, 'the animated width must be the clamped one').toMatch(/animate=\{\{ width: dockW/)
    expect(src, 'and so must the body').toMatch(/style=\{\{ width: dockW \}\}/)
    expect(src, 'aria-valuenow reports the CHOICE, not the clamp').toMatch(/aria-valuenow=\{Math\.round\(width\)\}/)
  })

  it('SidePanel now takes the clamp from the primitive instead of its own copy', () => {
    const src = read('shared/ui/SidePanel.tsx')
    expect(src).toMatch(/fitWidth: dockW/)
    expect(src, 'the local viewport state must be gone').not.toMatch(/setViewportW/)
    expect(src, 'and its hand-rolled clamp with it').not.toMatch(/Math\.min\(width, Math\.max\(0, viewportW/)
  })

  it('the primitive itself carries the mechanism', () => {
    const src = read('shared/ui/useResizablePanel.ts')
    expect(src).toMatch(/const fitWidth = fitPanel\(width, extent, opts\.edgePeek \?\? 32\)/)
    expect(read('shared/ui/resizeController.ts')).toMatch(/return Math\.min\(size, Math\.max\(0, viewport - peek\)\)/)
    expect(src, 'returned alongside the stored width, not instead of it').toMatch(/return \{ width, fitWidth,/)
  })
})
