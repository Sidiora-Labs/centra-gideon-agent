import { describe, expect, it, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import { act } from 'react'
import { SidePanel } from './SidePanel'


const STORE_KEY = 'sidepanel-clamp-test'
const EDGE_PEEK = 32

function setViewport(w: number) {
  ;(window as unknown as { innerWidth: number }).innerWidth = w
}

function renderedDockWidth(container: HTMLElement): number {
  const inner = container.querySelector<HTMLElement>('div.flex.h-full.flex-col')
  if (!inner) throw new Error('docked panel inner column not found — the selector no longer matches')
  const w = inner.style.width
  if (!/^\d+px$/.test(w)) throw new Error(`expected an explicit px width, got ${JSON.stringify(w)}`)
  return Number.parseInt(w, 10)
}

function mount(viewportW: number) {
  setViewport(viewportW)
  return render(
    <SidePanel title="Explorer" storeKey={STORE_KEY} fillHeight onClose={() => {}}>
      <p>body</p>
    </SidePanel>,
  )
}

describe('SidePanel clamps its docked width to the viewport', () => {
  beforeEach(() => {
    localStorage.clear()
    setViewport(1440)
  })

  it('does NOT engage on a desktop viewport — the stored width wins', () => {
    const { container } = mount(1440)
    expect(renderedDockWidth(container)).toBe(420)
  })

  it.each([
    [390, 390 - EDGE_PEEK],
    [320, 320 - EDGE_PEEK],
  ])('at %ipx the dock renders %ipx — inside the screen, not clipped by it', (vw, expected) => {
    const { container } = mount(vw)
    const w = renderedDockWidth(container)
    expect(w).toBe(expected)
    expect(w).toBeLessThanOrEqual(vw)
  })

  it('clamps a WIDER stored width without overwriting it — a wide screen restores the choice', () => {
    localStorage.setItem(STORE_KEY, '720')
    const { container } = mount(390)
    expect(renderedDockWidth(container)).toBe(390 - EDGE_PEEK)
    expect(localStorage.getItem(STORE_KEY)).toBe('720')
  })

  it('follows a resize — a rotation or a dragged window re-clamps', () => {
    const { container } = mount(1440)
    expect(renderedDockWidth(container)).toBe(420)
    act(() => {
      setViewport(360)
      window.dispatchEvent(new Event('resize'))
    })
    expect(renderedDockWidth(container)).toBe(360 - EDGE_PEEK)
    act(() => {
      setViewport(1440)
      window.dispatchEvent(new Event('resize'))
    })
    expect(renderedDockWidth(container)).toBe(420)
  })
})
