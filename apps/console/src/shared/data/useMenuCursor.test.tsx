import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ContextMenu } from '../ui/motion/ContextMenu'


const ITEMS = (onSelect = () => {}) => [
  { label: 'Open', onSelect },
  { label: 'Go to chat', onSelect: () => {} },
  { label: 'Delete', onSelect: () => {}, danger: true },
]

function openMenu(items = ITEMS()) {
  const view = render(
    <div>
      <button type="button">invoker</button>
      <ContextMenu items={items}><div>row body</div></ContextMenu>
    </div>,
  )
  const invoker = screen.getByRole('button', { name: 'invoker' })
  invoker.focus()
  act(() => { fireEvent.contextMenu(screen.getByText('row body')) })
  return { ...view, invoker }
}

const rows = () => screen.getAllByRole('menuitem')

describe('the cursor is focus', () => {
  it('opening the menu moves focus into it, onto the first row', () => {
    openMenu()
    expect(document.activeElement).toBe(rows()[0])
  })

  it('ArrowDown/ArrowUp move real focus, not just a font weight', () => {
    openMenu()
    act(() => { fireEvent.keyDown(document, { key: 'ArrowDown' }) })
    expect(document.activeElement).toBe(rows()[1])
    act(() => { fireEvent.keyDown(document, { key: 'ArrowDown' }) })
    expect(document.activeElement).toBe(rows()[2])
    act(() => { fireEvent.keyDown(document, { key: 'ArrowUp' }) })
    expect(document.activeElement).toBe(rows()[1])
  })

  it('clamps at both ends instead of wrapping — the pre-existing behaviour', () => {
    openMenu()
    act(() => { fireEvent.keyDown(document, { key: 'ArrowUp' }) })
    expect(document.activeElement).toBe(rows()[0])
    for (let i = 0; i < 5; i++) act(() => { fireEvent.keyDown(document, { key: 'ArrowDown' }) })
    expect(document.activeElement).toBe(rows()[2])
  })

  it('the menu is ONE tab stop: the cursor row roves, the rest sit at -1', () => {
    openMenu()
    expect(rows().map((r) => r.getAttribute('tabindex'))).toEqual(['0', '-1', '-1'])
    act(() => { fireEvent.keyDown(document, { key: 'ArrowDown' }) })
    expect(rows().map((r) => r.getAttribute('tabindex'))).toEqual(['-1', '0', '-1'])
  })

  it('the paint follows focus rather than leading it', () => {
    openMenu()
    act(() => { fireEvent.keyDown(document, { key: 'ArrowDown' }) })
    const painted = rows().findIndex((r) => r.querySelector('.bg-primary'))
    expect(painted).toBe(rows().indexOf(document.activeElement as HTMLElement))
  })
})

describe('Enter activates exactly once', () => {
  it('the document handler no longer duplicates the native button activation', () => {
    const onSelect = vi.fn()
    openMenu(ITEMS(onSelect))
    const cursor = document.activeElement as HTMLElement
    act(() => { fireEvent.keyDown(document, { key: 'Enter' }) })
    act(() => { fireEvent.click(cursor) })
    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it('the Enter branch is gone from the source, not just inert', () => {
    const src = readFileSync(join(process.cwd(), "src/shared/ui/motion/ContextMenu.tsx"), 'utf8')
    expect(src, 'a re-added Enter branch fires onSelect twice per press').not.toMatch(/key === 'Enter'/)
  })
})

describe('closing hands focus back', () => {
  it('Escape returns focus to the invoker instead of dropping it on <body>', () => {
    const { invoker } = openMenu()
    expect(document.activeElement).not.toBe(invoker)
    act(() => { fireEvent.keyDown(document, { key: 'Escape' }) })
    expect(document.activeElement).toBe(invoker)
  })

  it('Tab dismisses and returns focus, so the browser tabs on from the invoker', () => {
    const { invoker } = openMenu()
    act(() => { fireEvent.keyDown(document, { key: 'Tab' }) })
    expect(document.activeElement).toBe(invoker)
  })

  it('choosing a row returns focus too', () => {
    const { invoker } = openMenu()
    act(() => { fireEvent.click(rows()[0]) })
    expect(document.activeElement).toBe(invoker)
  })

  it('an outside CLICK does not yank focus back — the pointer is already elsewhere', () => {
    const { invoker } = openMenu()
    const cursor = document.activeElement
    act(() => { fireEvent.mouseDown(document.body) })
    expect(document.activeElement, 'focus stays where the user left it').toBe(cursor)
    expect(document.activeElement).not.toBe(invoker)
  })
})

describe('a disabled row stays reachable and says why it cannot act', () => {
  it('announces aria-disabled rather than dropping out of the cursor path', () => {
    const onSelect = vi.fn()
    render(
      <ContextMenu items={[{ label: 'Details', onSelect: () => {} }, { label: 'Install', onSelect, disabled: true }]}>
        <div>card</div>
      </ContextMenu>,
    )
    act(() => { fireEvent.contextMenu(screen.getByText('card')) })
    const install = screen.getByRole('menuitem', { name: 'Install' })
    expect(install).toHaveAttribute('aria-disabled', 'true')
    act(() => { fireEvent.keyDown(document, { key: 'ArrowDown' }) })
    expect(document.activeElement, 'APG: a disabled item is reachable, not skipped').toBe(install)
    act(() => { fireEvent.click(install) })
    expect(onSelect).not.toHaveBeenCalled()
  })
})

describe("FileTree's second implementation shares the keyboard contract", () => {
  const whole = readFileSync(join(process.cwd(), "src/features/files/browse/FileTree.tsx"), 'utf8')
  const at = whole.indexOf('function ContextMenu({ x, y')
  const src = whole.slice(at)

  it('the slice really is the menu and nothing else', () => {
    expect(at, 'the menu component moved — re-anchor this slice').toBeGreaterThan(0)
    expect(src).toContain('createPortal')
    expect(src, 'the slice must not swallow the tree rows that own Enter').not.toContain('commitRename')
  })

  it('uses the hook rather than a second cursor', () => {
    expect(src).toMatch(/useMenuCursor\(\{\s*containerRef: ref, count: items\.length/)
  })

  it('declares the roles its new focus behaviour promises', () => {
    expect(src).toMatch(/role="menu" aria-orientation="vertical"/)
    expect(src).toMatch(/role="menuitem" tabIndex=\{tabIndexFor\(i\)\}/)
  })

  it('arrow keys and a focus-returning Escape are wired', () => {
    expect(src).toMatch(/menuCursorKeydown\(e, \{ move, dismiss: closeAndReturnFocus \}\)/)
    expect(src).toMatch(/if \(e\.key === 'Escape'\) \{ e\.stopPropagation\(\); closeAndReturnFocus\(\); return \}/)
  })

  it('consumes Escape so it does not also collapse the Explorer', () => {
    expect(src).toMatch(/e\.stopPropagation\(\); closeAndReturnFocus/)
  })

  it('has no Enter branch of its own either', () => {
    expect(src).not.toMatch(/key === 'Enter'/)
  })
})

describe('every ContextMenu consumer inherits this — the census', () => {
  it('is worth doing in the primitive: 13 files, 16 call sites', () => {
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const PAGES = join(process.cwd(), "src/features")
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
      })
    const sites = walk(PAGES).flatMap((f) => [...readFileSync(f, 'utf8').matchAll(/<ContextMenu[\s>]/g)].map(() => f))
    expect(new Set(sites).size, 'files rendering a context menu').toBeGreaterThanOrEqual(13)
    expect(sites.length, 'call sites — one primitive, sixteen row menus').toBeGreaterThanOrEqual(16)
  })
})
