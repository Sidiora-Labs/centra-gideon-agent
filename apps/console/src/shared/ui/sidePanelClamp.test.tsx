import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react'
import { act } from 'react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SidePanel } from './SidePanel'

const STORE_KEY = 'sidepanel-clamp-test'
const sheetCSS = readFileSync(join(process.cwd(), 'src/shared/ui/mobileSheet.css'), 'utf8')
let originalWidth: PropertyDescriptor | undefined

function setViewport(width: number) {
  act(() => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
    window.dispatchEvent(new Event('resize'))
  })
}

function renderedDockWidth(): number {
  const region = screen.getByRole('region', { name: 'Explorer' })
  const inner = region.querySelector<HTMLElement>(':scope > div.flex.h-full.flex-col')
  if (!inner) throw new Error('docked panel inner column not found')
  expect(inner.style.width).toMatch(/^\d+px$/)
  return Number.parseInt(inner.style.width, 10)
}

function mount(viewportWidth: number) {
  setViewport(viewportWidth)
  return render(<SidePanel title="Explorer" storeKey={STORE_KEY} fillHeight onClose={() => {}}>
    <input aria-label="Panel search" defaultValue="retained search" />
    <p>body</p>
  </SidePanel>)
}

describe('SidePanel preserves the saved choice while fitting its actual responsive container', () => {
  beforeEach(() => {
    originalWidth = Object.getOwnPropertyDescriptor(window, 'innerWidth')
    localStorage.clear()
    setViewport(1440)
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    if (originalWidth) Object.defineProperty(window, 'innerWidth', originalWidth)
  })

  it('keeps the stored width on a desktop viewport', () => {
    localStorage.setItem(STORE_KEY, '540')
    mount(1440)
    expect(renderedDockWidth()).toBe(540)
  })

  it.each([390, 320])('at %ipx uses the bounded native modal sheet with reachable body and close control', width => {
    mount(width)
    expect(screen.queryByRole('region')).toBeNull()
    const sheet = screen.getByRole('dialog', { name: 'Explorer' })
    expect(sheet).toHaveAttribute('aria-modal', 'true')
    expect(sheet).toContainElement(screen.getByRole('textbox', { name: 'Panel search' }))
    expect(within(sheet).getByRole('button', { name: 'Close' })).toBeEnabled()
    expect(sheetCSS).toMatch(/\.gideon-mobile-sheet\s*\{[^}]*width:\s*100%/)
    expect(sheetCSS).toMatch(/\.gideon-mobile-sheet-overlay\s*\{[^}]*inset-inline:\s*0/)
    expect(sheetCSS).toMatch(/padding:\s*max\(8px,/)
    expect(sheetCSS).toMatch(/min-width:\s*0/)
  })

  it('keeps a wider saved choice after a narrow initial mount, settled persistence and desktop restoration', () => {
    vi.useFakeTimers()
    localStorage.setItem(STORE_KEY, '720')
    mount(320)
    act(() => vi.advanceTimersByTime(250))
    expect(localStorage.getItem(STORE_KEY)).toBe('720')
    setViewport(1440)
    expect(renderedDockWidth()).toBe(720)
    expect(screen.getByRole('textbox', { name: 'Panel search' })).toHaveValue('retained search')
  })

  it('re-clamps to half the workspace and restores the chosen width after rotation without overwriting it', () => {
    vi.useFakeTimers()
    localStorage.setItem(STORE_KEY, '720')
    mount(1440)
    expect(renderedDockWidth()).toBe(720)
    setViewport(900)
    expect(renderedDockWidth()).toBe(450)
    expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow', '450')
    expect(screen.getByRole('separator')).toHaveAttribute('aria-valuemax', '450')
    act(() => vi.advanceTimersByTime(250))
    expect(localStorage.getItem(STORE_KEY)).toBe('720')
    setViewport(320)
    expect(screen.getByRole('dialog', { name: 'Explorer' })).toBeInTheDocument()
    setViewport(1440)
    expect(renderedDockWidth()).toBe(720)
  })

  it('keeps keyboard resize bounded and the focused separator operable', () => {
    mount(1440)
    const handle = screen.getByRole('separator', { name: /Resize panel.*arrow keys/ })
    handle.focus()
    fireEvent.keyDown(handle, { key: 'End' })
    expect(renderedDockWidth()).toBe(720)
    expect(handle).toHaveFocus()
    fireEvent.keyDown(handle, { key: 'Home' })
    expect(renderedDockWidth()).toBe(320)
    fireEvent.keyDown(handle, { key: 'ArrowLeft' })
    expect(renderedDockWidth()).toBe(336)
    expect(handle).toHaveAttribute('aria-valuenow', '336')
  })
})
