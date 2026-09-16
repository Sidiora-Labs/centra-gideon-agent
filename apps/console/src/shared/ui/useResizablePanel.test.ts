import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useResizablePanel } from './useResizablePanel'


const setH = (h: number) => Object.defineProperty(window, 'innerHeight', { value: h, configurable: true })

describe('useResizablePanel — dynamic max + storageKey (the terminal-drawer capabilities)', () => {
  beforeEach(() => { localStorage.clear(); setH(900) })
  afterEach(() => { localStorage.clear() })

  it('persists to the OVERRIDE key, never to `${key}-w`', () => {
    const { result, unmount } = renderHook(() =>
      useResizablePanel('terminal-drawer', { storageKey: 'terminal-drawer-h', def: 320, min: 160, max: () => window.innerHeight * 0.85, side: 'bottom' }))
    act(() => { result.current.onHandleKey({ key: 'ArrowUp', preventDefault() {} } as unknown as React.KeyboardEvent) })
    unmount()
    expect(localStorage.getItem('terminal-drawer-h'), 'writes the legacy key').toBe('336')
    expect(localStorage.getItem('terminal-drawer-w'), 'must NOT write the -w convention key').toBeNull()
    expect(localStorage.getItem('terminal-drawer-collapsed'), 'width-only: no collapsed key').toBeNull()
  })

  it('reads the OVERRIDE key on init — a saved size round-trips, not resets', () => {
    localStorage.setItem('terminal-drawer-h', '300')
    const { result } = renderHook(() =>
      useResizablePanel('terminal-drawer', { storageKey: 'terminal-drawer-h', def: 320, min: 160, max: () => window.innerHeight * 0.85, side: 'bottom' }))
    expect(result.current.width).toBe(300)
  })

  it('End clamps to the LIVE dynamic max, and the returned max tracks a resize', () => {
    const { result } = renderHook(() =>
      useResizablePanel('terminal-drawer', { storageKey: 'terminal-drawer-h', def: 320, min: 160, max: () => window.innerHeight * 0.85, side: 'bottom' }))
    expect(result.current.max).toBe(765)
    act(() => { result.current.onHandleKey({ key: 'End', preventDefault() {} } as unknown as React.KeyboardEvent) })
    expect(result.current.width, 'End → the current ceiling').toBe(765)
    act(() => { setH(600); window.dispatchEvent(new Event('resize')) })
    expect(result.current.max, 'aria-valuemax tracks the viewport').toBe(510)
    act(() => { result.current.onHandleKey({ key: 'End', preventDefault() {} } as unknown as React.KeyboardEvent) })
    expect(result.current.width, 'End clamps to the SHRUNK ceiling').toBe(510)
  })

  it('a STATIC max still works and adds no resize listener churn (the other four adopters)', () => {
    const { result } = renderHook(() =>
      useResizablePanel('side', { def: 420, min: 320, max: 720, side: 'right' }))
    expect(result.current.max).toBe(720)
    act(() => { result.current.onHandleKey({ key: 'End', preventDefault() {} } as unknown as React.KeyboardEvent) })
    expect(result.current.width).toBe(720)
    act(() => { setH(400); window.dispatchEvent(new Event('resize')) })
    expect(result.current.max).toBe(720)
  })
})
