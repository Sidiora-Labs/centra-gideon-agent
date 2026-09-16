import { useRef, useState, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { useResizablePanel } from './useResizablePanel'
import { useFocusTrap } from './useFocusTrap'
import { useFocusReturn } from './useFocusReturn'
import { Popover } from './Popover'
import { menuCursorKeydown, useMenuCursor } from '../data/useMenuCursor'
import { focusCandidates } from './focusNavigation'
import { keyboardSize, type PanelSide } from './resizeController'

function ResizePanel({ side = 'right', collapsible = false }: { side?: PanelSide; collapsible?: boolean }) {
  const panel = useResizablePanel('hook-case', { def: 400, min: 200, max: 800, side, collapsible })
  return <>
    <div role="separator" tabIndex={0} aria-valuenow={panel.width} aria-valuemin={panel.min} aria-valuemax={panel.max}
      onPointerDown={panel.onHandleDown} onKeyDown={panel.onHandleKey} />
    <button onClick={() => panel.setCollapsed((current) => !current)}>{panel.collapsed ? 'Collapsed' : 'Expanded'}</button>
  </>
}
const pointer = (target: EventTarget, type: string, position: { x?: number; y?: number; id?: number } = {}) => {
  const event = new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: position.id ?? 7, clientX: position.x ?? 200, clientY: position.y ?? 200 })
  act(() => { target.dispatchEvent(event) })
}

beforeEach(() => {
  localStorage.removeItem('hook-case-w')
  localStorage.removeItem('hook-case-collapsed')
  document.body.style.userSelect = ''
})

afterEach(() => {
  localStorage.removeItem('hook-case-w')
  localStorage.removeItem('hook-case-collapsed')
  document.body.style.userSelect = ''
})

describe('resize session lifetime', () => {
  it.each<PanelSide>(['left', 'right', 'top', 'bottom'])('moves a %s panel along its own axis', (side) => {
    render(<ResizePanel side={side} />)
    const handle = screen.getByRole('separator')
    pointer(handle, 'pointerdown')
    pointer(handle, 'pointermove', { x: 240, y: 260 })
    const expected = { left: 440, right: 360, top: 460, bottom: 340 }[side]
    expect(handle).toHaveAttribute('aria-valuenow', String(expected))
    pointer(handle, 'pointerup')
    pointer(handle, 'pointermove', { x: 300, y: 300 })
    expect(handle).toHaveAttribute('aria-valuenow', String(expected))
  })

  it('ignores another pointer and restores the prior selection style on cancellation', () => {
    document.body.style.userSelect = 'text'
    render(<ResizePanel />)
    const handle = screen.getByRole('separator')
    pointer(handle, 'pointerdown')
    expect(document.body.style.userSelect).toBe('none')
    pointer(handle, 'pointermove', { x: 0, id: 9 })
    expect(handle).toHaveAttribute('aria-valuenow', '400')
    pointer(handle, 'pointercancel', { id: 9 })
    expect(document.body.style.userSelect).toBe('none')
    pointer(handle, 'pointercancel')
    expect(document.body.style.userSelect).toBe('text')
    pointer(handle, 'pointermove', { x: 0 })
    expect(handle).toHaveAttribute('aria-valuenow', '400')
  })

  it('unmount ends an active drag and flushes its final width immediately', () => {
    document.body.style.userSelect = 'contain'
    const { unmount } = render(<ResizePanel />)
    const handle = screen.getByRole('separator')
    pointer(handle, 'pointerdown')
    pointer(handle, 'pointermove', { x: 100 })
    expect(handle).toHaveAttribute('aria-valuenow', '500')
    unmount()
    expect(document.body.style.userSelect).toBe('contain')
    expect(localStorage.getItem('hook-case-w')).toBe('500')
    pointer(window, 'pointermove', { x: 0 })
    expect(localStorage.getItem('hook-case-w')).toBe('500')
  })

  it('coalesces size changes until settled, but collapse persists immediately', async () => {
    localStorage.setItem('hook-case-w', '400')
    render(<ResizePanel collapsible />)
    const handle = screen.getByRole('separator')
    fireEvent.keyDown(handle, { key: 'ArrowLeft' })
    fireEvent.keyDown(handle, { key: 'ArrowLeft', shiftKey: true })
    expect(handle).toHaveAttribute('aria-valuenow', '464')
    expect(localStorage.getItem('hook-case-w')).toBe('400')
    fireEvent.click(screen.getByText('Expanded'))
    expect(localStorage.getItem('hook-case-collapsed')).toBe('1')
    await act(() => new Promise((resolve) => setTimeout(resolve, 230)))
    expect(localStorage.getItem('hook-case-w')).toBe('464')
  })

  it('falls back from invalid storage and leaves unrelated keys to the browser', () => {
    localStorage.setItem('hook-case-w', 'not-a-number')
    render(<ResizePanel />)
    const handle = screen.getByRole('separator')
    expect(handle).toHaveAttribute('aria-valuenow', '400')
    const event = new KeyboardEvent('keydown', { key: 'a', bubbles: true, cancelable: true })
    fireEvent(handle, event)
    expect(event.defaultPrevented).toBe(false)
    expect(localStorage.getItem('hook-case-collapsed')).toBeNull()
  })

  it('keeps directional and cross-axis keyboard semantics with bounded steps', () => {
    const bounds = { min: 200, max: 800 }
    expect(keyboardSize('ArrowRight', false, 400, 'left', bounds)).toBe(416)
    expect(keyboardSize('ArrowDown', true, 400, 'right', bounds)).toBe(352)
    expect(keyboardSize('ArrowRight', true, 780, 'bottom', bounds)).toBe(800)
    expect(keyboardSize('Home', false, 400, 'top', bounds)).toBe(200)
    expect(keyboardSize('Escape', false, 400, 'top', bounds)).toBeNull()
  })
})

function Trap({ children, name = 'Scope' }: { children?: ReactNode; name?: string }) {
  const ref = useFocusTrap<HTMLDivElement>()
  return <div ref={ref} role="dialog" aria-label={name}>{children}</div>
}

describe('focus scope ownership', () => {
  it('discovers visible enabled tab stops in native tabindex order', () => {
    const { container } = render(<div>
      <button>Default</button><button tabIndex={2}>Second</button><button tabIndex={1}>First</button>
      <button disabled>Disabled</button><button tabIndex={-1}>Excluded</button>
      <div hidden><button>Hidden</button></div><div style={{ display: 'none' }}><button>Display none</button></div>
      <div inert><button>Inert</button></div>
    </div>)
    expect(focusCandidates(container).map((element) => element.textContent)).toEqual(['First', 'Second', 'Default'])
  })

  it('acquires focus, walks both directions and wraps', () => {
    render(<><button>Outside</button><Trap><button>First</button><button>Last</button></Trap></>)
    const first = screen.getByText('First')
    const last = screen.getByText('Last')
    expect(document.activeElement).toBe(first)
    fireEvent.keyDown(first, { key: 'Tab' })
    expect(document.activeElement).toBe(last)
    fireEvent.keyDown(last, { key: 'Tab' })
    expect(document.activeElement).toBe(first)
    fireEvent.keyDown(first, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
  })

  it('lets a portaled popover own focus and native Tab while it is open', () => {
    render(<Trap><Popover portal trigger={(_open, toggle) => <button onClick={toggle}>Open choices</button>}>
      {() => <button>Portaled choice</button>}
    </Popover></Trap>)
    fireEvent.click(screen.getByText('Open choices'))
    expect(document.activeElement).toBe(screen.getByText('Portaled choice'))
    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    fireEvent(screen.getByText('Portaled choice'), event)
    expect(event.defaultPrevented).toBe(false)
    expect(document.activeElement).toBe(screen.getByText('Open choices'))
  })

  it('lets a child input consume Tab before the trap moves focus', () => {
    render(<Trap><textarea aria-label="Editor" onKeyDown={(event) => {
      if (event.key === 'Tab') event.preventDefault()
    }} /><button>After editor</button></Trap>)
    const editor = screen.getByLabelText('Editor')
    expect(document.activeElement).toBe(editor)
    fireEvent.keyDown(editor, { key: 'Tab' })
    expect(document.activeElement).toBe(editor)
  })

  it('preserves child autofocus and focuses the container when it has no tab stops', () => {
    const view = render(<Trap><button>First</button><input aria-label="Preferred" autoFocus /></Trap>)
    expect(document.activeElement).toBe(screen.getByLabelText('Preferred'))
    view.unmount()
    render(<Trap />)
    expect(document.activeElement).toBe(screen.getByRole('dialog'))
    fireEvent.keyDown(document.activeElement!, { key: 'Tab' })
    expect(document.activeElement).toBe(screen.getByRole('dialog'))
  })

  it('lets the nested scope own focus and returns to its invoker when it closes', () => {
    function Nested() {
      const [open, setOpen] = useState(false)
      return <Trap name="Outer"><button onClick={() => setOpen(true)}>Open inner</button>
        {open && <Trap name="Inner"><button onClick={() => setOpen(false)}>Close inner</button><button>Inner last</button></Trap>}
      </Trap>
    }
    render(<Nested />)
    const trigger = screen.getByText('Open inner')
    fireEvent.click(trigger)
    expect(document.activeElement).toBe(screen.getByText('Close inner'))
    fireEvent.keyDown(document.activeElement!, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(screen.getByText('Inner last'))
    fireEvent.click(screen.getByText('Close inner'))
    expect(document.activeElement).toBe(trigger)
  })

  it('captures the return target before a nonmodal child autofocus runs', () => {
    function Panel() {
      const ref = useFocusReturn<HTMLDivElement>()
      return <div ref={ref}><input autoFocus aria-label="Panel field" /></div>
    }
    function Host({ visible }: { visible: boolean }) {
      return <><button>Invoker</button>{visible && <Panel />}</>
    }
    const { rerender } = render(<Host visible={false} />)
    const invoker = screen.getByText('Invoker')
    invoker.focus()
    rerender(<Host visible />)
    expect(document.activeElement).toBe(screen.getByLabelText('Panel field'))
    rerender(<Host visible={false} />)
    expect(document.activeElement).toBe(invoker)
  })

  it('returns to the external invoker when an entire nested dialog tree unmounts', () => {
    function Outer() {
      const [inner, setInner] = useState(false)
      return <Trap name="Outer"><button onClick={() => setInner(true)}>Open nested</button>
        {inner && <Trap name="Inner"><button>Nested action</button></Trap>}
      </Trap>
    }
    function Host({ visible }: { visible: boolean }) {
      return <><button>External invoker</button>{visible && <Outer />}</>
    }
    const { rerender } = render(<Host visible={false} />)
    const invoker = screen.getByText('External invoker')
    invoker.focus()
    rerender(<Host visible />)
    fireEvent.click(screen.getByText('Open nested'))
    expect(document.activeElement).toBe(screen.getByText('Nested action'))
    rerender(<Host visible={false} />)
    expect(document.activeElement).toBe(invoker)
  })
})

function Menu({ count = 3, openKey = 'one', autoFocus = true, initialIndex = 0 }: {
  count?: number; openKey?: string | null; autoFocus?: boolean; initialIndex?: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const cursor = useMenuCursor({ containerRef: ref, count, openKey, autoFocus, initialIndex })
  return <div ref={ref} role="menu" onKeyDown={(event) => menuCursorKeydown(event.nativeEvent, {
    move: cursor.move, dismiss: cursor.restoreFocus,
  })}>
    {Array.from({ length: count }, (_, index) => <button key={index} role="menuitem" tabIndex={cursor.tabIndexFor(index)}
      aria-disabled={index === 1} onClick={cursor.restoreFocus}>Row {index}</button>)}
  </div>
}

describe('menu cursor state and ownership', () => {
  it('clamps on count changes and resets on a fresh open key', () => {
    const { rerender } = render(<Menu initialIndex={2} />)
    expect(document.activeElement).toBe(screen.getByText('Row 2'))
    rerender(<Menu count={1} initialIndex={2} />)
    expect(document.activeElement).toBe(screen.getByText('Row 0'))
    expect(screen.getByText('Row 0')).toHaveAttribute('tabindex', '0')
    rerender(<Menu openKey="two" initialIndex={1} />)
    expect(document.activeElement).toBe(screen.getByText('Row 1'))
  })

  it('hover opening waits for keyboard entry and the first arrow keeps the selected row', () => {
    render(<><button>Trigger</button><Menu autoFocus={false} initialIndex={1} /></>)
    const trigger = screen.getByText('Trigger')
    trigger.focus()
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowDown' })
    expect(document.activeElement).toBe(screen.getByText('Row 1'))
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' })
    expect(document.activeElement).toBe(screen.getByText('Row 2'))
  })

  it('returns to the original invoker after repositioning while focus is in the menu', () => {
    function Host({ openKey }: { openKey: string | null }) {
      return <><button>Trigger</button><Menu openKey={openKey} /></>
    }
    const { rerender } = render(<Host openKey={null} />)
    const trigger = screen.getByText('Trigger')
    trigger.focus()
    rerender(<Host openKey="first" />)
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'End' })
    rerender(<Host openKey="second" />)
    expect(document.activeElement).toBe(screen.getByText('Row 0'))
    fireEvent.click(screen.getByText('Row 0'))
    expect(document.activeElement).toBe(trigger)
  })

  it('respects consumed keys and dismisses Tab without cancelling native traversal', () => {
    const moves: number[] = []
    const dismissals: boolean[] = []
    const commands = { move: (delta: number) => moves.push(delta), dismiss: () => dismissals.push(true) }
    const owned = new KeyboardEvent('keydown', { key: 'ArrowDown', cancelable: true })
    owned.preventDefault()
    expect(menuCursorKeydown(owned, commands)).toBe(false)
    expect(moves).toEqual([])
    const tab = new KeyboardEvent('keydown', { key: 'Tab', cancelable: true })
    expect(menuCursorKeydown(tab, commands)).toBe(true)
    expect(tab.defaultPrevented).toBe(false)
    expect(dismissals).toEqual([true])
    expect(menuCursorKeydown(new KeyboardEvent('keydown', { key: 'Enter' }), commands)).toBe(false)
  })
})
