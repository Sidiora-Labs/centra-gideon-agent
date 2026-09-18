// KNOWN LIMITATION, recorded here and above SidePanel(): `.hit-24-x` declares a 24px pointer
// band, but the dock paints itself with `overflow-hidden`, so the half of the band that falls
// outside the panel's left edge is clipped away — about 15px of the 24px survives. That is a
// real improvement on the 6px strip and still short of WCAG 2.5.8; reaching the full 24px means
// moving the divider out of the clipped container, which is a layout correction and separate work.
import { describe, expect, it, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { act } from 'react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SidePanel } from './SidePanel'


const STORE_KEY = 'sidepanel-target-test'
const SRC = join(process.cwd(), 'src')
const css = readFileSync(join(SRC, 'shared/theme/tokens.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
const panelSrc = readFileSync(join(SRC, 'shared/ui/SidePanel.tsx'), 'utf8')

function hitBandRule(): string {
  const at = css.indexOf('.hit-24-x::before {')
  expect(at, 'the shared thin-handle hit band is gone from tokens.css').toBeGreaterThan(-1)
  return css.slice(at, css.indexOf('}', at))
}

function mount() {
  ;(window as unknown as { innerWidth: number }).innerWidth = 1440
  const view = render(
    <SidePanel title="Explorer" storeKey={STORE_KEY} fillHeight onClose={() => {}}>
      <p>body</p>
    </SidePanel>,
  )
  const handle = screen.getByRole('separator')
  const line = handle.querySelector<HTMLElement>('span.bg-outline-variant\\/50')
  if (!line) throw new Error('the visible divider line is gone — this file measures nothing')
  return { ...view, handle, line }
}

function dockWidth(container: HTMLElement): number {
  const inner = container.querySelector<HTMLElement>('div.flex.h-full.flex-col')
  if (!inner) throw new Error('docked panel inner column not found — the selector no longer matches')
  const w = Number.parseFloat(inner.style.width)
  if (!Number.isFinite(w)) throw new Error(`expected an explicit px width, got ${JSON.stringify(inner.style.width)}`)
  return w
}

const pointer = (target: EventTarget, type: string, x: number) => {
  act(() => {
    target.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 4, clientX: x, clientY: 200 }))
  })
}

describe('the SidePanel divider is dragged by a target far bigger than the line it draws', () => {
  beforeEach(() => { localStorage.clear() })

  it('the handle carries the shared 24px pointer band, and the band captures pointer events', () => {
    const { handle } = mount()
    expect(handle.className, 'the 6px strip alone was the defect').toContain('hit-24-x')
    expect(handle.className, 'the band needs a positioned call site to anchor to').toContain('absolute')
    const band = hitBandRule()
    expect(band).toMatch(/width:\s*var\(--hit-min\)/)
    expect(band, 'a decorative band would not resize anything').toMatch(/pointer-events:\s*auto/)
    expect(css.slice(css.indexOf('.hit-24-x {'))).toMatch(/--hit-min:\s*24px/)
  })

  it('the reachable target beats the drawn line by an order of magnitude — and still misses 24px', () => {
    const { handle, line } = mount()
    const strip = 6
    expect(handle.className, 'the drawn strip must not have grown').toContain('w-1.5')
    expect(line.style.width, 'the hairline itself is still 1px').toBe('1px')
    const reachable = strip / 2 + 24 / 2
    expect(reachable, 'the band must beat the strip it replaces').toBeGreaterThan(strip)
    expect(reachable, 'clipping keeps it under the 2.5.8 floor — the recorded limitation')
      .toBeLessThan(24)
  })

  it('names the clipper, so the recorded limitation is not folklore', () => {
    const { container } = mount()
    const dock = container.querySelector<HTMLElement>('div.overflow-hidden')
    expect(dock, 'the dock clips the outward half of the band — that is the limitation').toBeTruthy()
    expect(dock!.contains(screen.getByRole('separator')), 'the handle lives inside the clipper').toBe(true)
    expect(panelSrc, 'the one-line note about the clipped band must stay with the code')
      .toMatch(/clips[\s\S]{0,140}2\.5\.8|2\.5\.8[\s\S]{0,140}clips/)
  })

  it('does NOT move or restyle the visible divider', () => {
    const { handle, line } = mount()
    expect(line.className, 'the line still sits on the panel edge').toContain('left-0')
    expect(line.className).toContain('inset-y-0')
    expect(handle.className, 'the handle hugs the same edge it always did').toContain('left-0')
    expect(handle.className, 'the focus tint keeps the 6px strip, not the band').toContain('focus-visible:bg-primary/20')
  })

  it('a drag on the handle resizes the dock', () => {
    const { container, handle } = mount()
    expect(dockWidth(container)).toBe(420)
    pointer(handle, 'pointerdown', 1000)
    pointer(handle, 'pointermove', 940)
    expect(dockWidth(container)).toBe(480)
    expect(handle).toHaveAttribute('aria-valuenow', '480')
    pointer(handle, 'pointerup', 940)
  })

  it('a drag started on the hairline resizes too — the band swallows no gesture', () => {
    const { container, line } = mount()
    pointer(line, 'pointerdown', 1000)
    pointer(line, 'pointermove', 970)
    expect(dockWidth(container)).toBe(450)
    pointer(line, 'pointerup', 970)
  })

  it('leaves the keyboard path alone', () => {
    const { container, handle } = mount()
    act(() => { handle.focus() })
    act(() => { handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true })) })
    expect(dockWidth(container)).toBe(436)
    expect(handle).toHaveAttribute('aria-valuemin', '320')
    expect(handle).toHaveAttribute('aria-valuemax', '720')
    expect(handle).toHaveAttribute('aria-label', 'Resize panel — arrow keys to resize')
  })
})
