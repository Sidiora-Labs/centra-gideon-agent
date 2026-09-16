import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, act, cleanup, fireEvent } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { Popover } from './Popover'


afterEach(() => cleanup())

const isOpen = () => screen.getByText('Open').getAttribute('data-open')

function RoleLess({ after = true }: { after?: boolean }) {
  return (
    <div>
      <Popover portal trigger={(o, toggle) => <button onClick={toggle} data-open={o}>Open</button>}>
        {(close) => (
          <div>
            <button onClick={close}>First row</button>
            <button onClick={close}>Second row</button>
          </div>
        )}
      </Popover>
      {after && <button>Next on page</button>}
    </div>
  )
}

function RoleDeclaring() {
  return (
    <Popover portal trigger={(o, toggle) => <button onClick={toggle} data-open={o}>Open</button>}>
      {() => (
        <div role="listbox" aria-label="Options">
          <button role="option" aria-selected={false}>First option</button>
          { }
          <button role="option" aria-selected>Selected option</button>
        </div>
      )}
    </Popover>
  )
}

const open = () => act(() => { screen.getByText('Open').click() })

describe('a portaled Popover hands focus to its flyout', () => {
  it('moves focus INTO the flyout on open, instead of leaving it on the trigger', () => {
    render(<RoleLess />)
    open()
    expect(document.activeElement, 'focus must enter the flyout').toBe(screen.getByText('First row'))
  })

  it('🪤 lands on the FIRST TABBABLE, skipping a tabindex="-1" decoration', () => {
    render(
      <Popover portal trigger={(_o, toggle) => <button onClick={toggle}>Open</button>}>
        {() => (
          <div>
            <div tabIndex={-1} data-testid="deco">not a stop</div>
            <button>Real row</button>
          </div>
        )}
      </Popover>,
    )
    open()
    expect(document.activeElement).toBe(screen.getByText('Real row'))
    expect(document.activeElement).not.toBe(screen.getByTestId('deco'))
  })

  it('Tab closes the flyout and returns focus to the trigger', () => {
    render(<RoleLess />)
    open()
    fireEvent.keyDown(screen.getByText('First row'), { key: 'Tab' })
    expect(isOpen(), 'the flyout must close').toBe('false')
    expect(document.activeElement, 'focus returns to the trigger').toBe(screen.getByText('Open'))
  })

  it('🔑 Tab is NOT preventDefault-ed — the browser continues from the trigger', () => {
    render(<RoleLess />)
    open()
    const ev = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    act(() => { screen.getByText('First row').dispatchEvent(ev) })
    expect(ev.defaultPrevented, 'Tab must stay cancelable so the browser moves focus on').toBe(false)
  })

  it('Escape still closes and restores the trigger — the pre-existing behaviour is intact', () => {
    render(<RoleLess />)
    open()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(isOpen()).toBe('false')
    expect(document.activeElement).toBe(screen.getByText('Open'))
  })
})

describe('🔑 it stands down for a popup that owns its own keyboard', () => {
  it('does NOT move focus into a role="listbox" flyout', () => {
    render(<RoleDeclaring />)
    open()
    expect(document.activeElement, 'the cursor hook owns entry here').not.toBe(screen.getByText('First option'))
  })

  it('does NOT consume Tab inside a role-declaring flyout', () => {
    render(<RoleDeclaring />)
    open()
    fireEvent.keyDown(screen.getByText('First option'), { key: 'Tab' })
    expect(isOpen(), 'the flyout must be left alone').toBe('true')
  })
})

describe('inline mode is untouched', () => {
  it('does not move focus, because the flyout is already the trigger’s next sibling', () => {
    render(
      <Popover trigger={(_o, toggle) => <button onClick={toggle}>Open</button>}>
        {() => <button>Inline row</button>}
      </Popover>,
    )
    open()
    expect(document.activeElement).not.toBe(screen.getByText('Inline row'))
  })

  it('does not consume Tab either', () => {
    render(
      <Popover trigger={(_o, toggle) => <button onClick={toggle}>Open</button>}>
        {() => <button>Inline row</button>}
      </Popover>,
    )
    open()
    fireEvent.keyDown(screen.getByText('Inline row'), { key: 'Tab' })
    expect(screen.queryByText('Inline row')).toBeInTheDocument()
  })
})

describe('the fix is structural, and its reach is pinned', () => {
  const SRC = join(process.cwd(), "src")
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('focuses with preventScroll — without it, opening the menu closes it', () => {
    const code = strip(readFileSync(join(SRC, "shared/ui", 'Popover.tsx'), 'utf8'))
    expect(code).toContain('enterPopup(menu.current)')
    const controller = strip(readFileSync(join(SRC, 'shared/ui/popupController.ts'), 'utf8'))
    expect(controller).toContain('if (!element || ownsPopupKeyboard(element)) return')
    expect(controller).toMatch(/focusCandidates\(element\)\[0\]\?\.focus\(\{ preventScroll: true \}\)/)
  })

  it('VACUITY: the portaled call sites the fix serves are still there', () => {
    const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
    })
    let portaled = 0
    let callSites = 0
    for (const f of walk(SRC)) {
      const code = strip(readFileSync(f, 'utf8'))
      const popovers = [...code.matchAll(/<Popover\b/g)].length
      if (!popovers) continue
      callSites += popovers
      portaled += [...code.matchAll(/\bportal\b(?!\s*[:?])/g)].length
    }
    expect(portaled, 'portaled Popover call sites').toBeGreaterThanOrEqual(10)
    expect(callSites, 'Popover call sites overall').toBeGreaterThanOrEqual(13)
    expect(portaled).toBeGreaterThan(callSites / 2)
  })
})
