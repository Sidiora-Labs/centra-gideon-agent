import { useState } from 'react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MobileSheet } from './MobileSheet'

const LONG_TITLE = 'A saved automation with a very long name that must remain readable while the share and close controls stay reachable on a narrow phone'
const css = readFileSync(join(process.cwd(), 'src/shared/ui/mobileSheet.css'), 'utf8')

function SheetHost({ fullHeight = false }: { fullHeight?: boolean }) {
  const [open, setOpen] = useState(false)
  return <div className="gideon-shell">
    <button onClick={() => setOpen(true)}>Open sheet</button>
    <textarea aria-label="Background draft" defaultValue="keep this draft" />
    {open && <MobileSheet title={LONG_TITLE} icon={<span aria-hidden="true">◈</span>} fullHeight={fullHeight}
      actions={<><button>Share</button><button>Copy link</button></>} onClose={() => setOpen(false)}>
      <div>
        {Array.from({ length: 60 }, (_, index) => <p key={index}>Content row {index + 1}</p>)}
        <input aria-label="Edit last row" />
      </div>
    </MobileSheet>}
  </div>
}

describe('mobile sheet long-content behavior', () => {
  it.each([false, true])('keeps the full title, actions, and last body control reachable; fullHeight=%s', (fullHeight) => {
    const host = render(<SheetHost fullHeight={fullHeight} />)
    const trigger = screen.getByRole('button', { name: 'Open sheet' })
    trigger.focus()
    fireEvent.click(trigger)
    const dialog = screen.getByRole('dialog', { name: LONG_TITLE })
    const overlay = dialog.closest('[data-mobile-sheet-overlay]')
    if (fullHeight) expect(overlay).toHaveAttribute('data-full-height', 'true')
    else expect(overlay).not.toHaveAttribute('data-full-height')
    expect(host.container.contains(dialog)).toBe(false)
    expect(host.container.querySelector('.gideon-shell')).toHaveAttribute('inert')
    const heading = within(dialog).getByRole('heading', { level: 2, name: LONG_TITLE })
    expect(heading).toHaveAttribute('title', LONG_TITLE)
    expect(heading).toHaveAttribute('tabindex', '0')
    expect(heading).toHaveFocus()
    expect(within(dialog).getByRole('button', { name: 'Share' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Copy link' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Close' })).toBeInTheDocument()
    const body = dialog.querySelector('.gideon-mobile-sheet-body')
    expect(body).toContainElement(within(dialog).getByText('Content row 60'))
    expect(body).toContainElement(within(dialog).getByRole('textbox', { name: 'Edit last row' }))
    fireEvent.keyDown(heading, { key: 'Tab' })
    expect(within(dialog).getByRole('button', { name: 'Share' })).toHaveFocus()
    fireEvent.keyDown(document.activeElement!, { key: 'Tab' })
    expect(within(dialog).getByRole('button', { name: 'Copy link' })).toHaveFocus()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(host.container.querySelector('.gideon-shell')).not.toHaveAttribute('inert')
    expect(trigger).toHaveFocus()
    expect(screen.getByRole('textbox', { name: 'Background draft' })).toHaveValue('keep this draft')
    host.unmount()
  })

  it('lets the backdrop dismiss a regular sheet without changing its background', () => {
    render(<SheetHost />)
    fireEvent.click(screen.getByRole('button', { name: 'Open sheet' }))
    const overlay = screen.getByRole('dialog').closest('[data-mobile-sheet-overlay]')!
    fireEvent.click(overlay.querySelector('.gideon-mobile-sheet-backdrop')!)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('textbox', { name: 'Background draft' })).toHaveValue('keep this draft')
  })

  it('does not turn a short or composed title into an unnecessary scroll stop', () => {
    const { rerender } = render(<MobileSheet title="Short" onClose={() => {}}>Body</MobileSheet>)
    expect(screen.getByRole('heading', { name: 'Short' })).not.toHaveAttribute('tabindex')
    rerender(<MobileSheet title={<span>Composed title</span>} onClose={() => {}}>Body</MobileSheet>)
    const heading = screen.getByRole('heading', { name: 'Composed title' })
    expect(heading).not.toHaveAttribute('tabindex')
    expect(heading).not.toHaveAttribute('title')
  })

  it('keeps the outer sheet active while a nested sheet takes and returns focus', () => {
    function Nested() {
      const [outer, setOuter] = useState(true)
      const [inner, setInner] = useState(false)
      return <div className="gideon-shell">
        <button>Workspace</button>
        {outer && <MobileSheet title="Outer" onClose={() => setOuter(false)}>
          <button onClick={() => setInner(true)}>Inspect nested</button>
          {inner && <MobileSheet title="Inner" onClose={() => setInner(false)}>
            <button>Inner detail</button>
          </MobileSheet>}
        </MobileSheet>}
      </div>
    }
    const host = render(<Nested />)
    const outer = screen.getByRole('dialog', { name: 'Outer' })
    const opener = within(outer).getByRole('button', { name: 'Inspect nested' })
    opener.focus()
    fireEvent.click(opener)
    const inner = screen.getByRole('dialog', { name: 'Inner' })
    expect(outer.closest('[data-mobile-sheet-overlay]')).toHaveAttribute('inert')
    expect(host.container.querySelector('.gideon-shell')).toHaveAttribute('inert')
    fireEvent.click(within(inner).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog', { name: 'Inner' })).toBeNull()
    expect(outer.closest('[data-mobile-sheet-overlay]')).not.toHaveAttribute('inert')
    expect(host.container.querySelector('.gideon-shell')).toHaveAttribute('inert')
    expect(opener).toHaveFocus()
    fireEvent.click(within(outer).getByRole('button', { name: 'Close' }))
    expect(host.container.querySelector('.gideon-shell')).not.toHaveAttribute('inert')
    host.unmount()
  })

  it('restores a surviving trigger when a sheet action replaces its own sheet', () => {
    function Replace() {
      const [step, setStep] = useState<'closed' | 'actions' | 'activity'>('closed')
      return <div className="gideon-shell">
        <button onClick={() => setStep('actions')}>Open actions</button>
        {step === 'actions' && <MobileSheet title="Actions" onClose={() => setStep('closed')}>
          <button onClick={() => setStep('activity')}>Open activity</button>
        </MobileSheet>}
        {step === 'activity' && <MobileSheet title="Activity" onClose={() => setStep('closed')}>
          <button>Latest event</button>
        </MobileSheet>}
      </div>
    }
    const host = render(<Replace />)
    const trigger = screen.getByRole('button', { name: 'Open actions' })
    trigger.focus()
    fireEvent.click(trigger)
    const replace = screen.getByRole('button', { name: 'Open activity' })
    replace.focus()
    fireEvent.click(replace)
    const activity = screen.getByRole('dialog', { name: 'Activity' })
    fireEvent.click(within(activity).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(trigger).toHaveFocus()
    host.unmount()
  })

  it('preserves a background that was already inert before the sheet opened', () => {
    const shell = document.createElement('div')
    shell.className = 'gideon-shell'
    shell.setAttribute('inert', '')
    document.body.appendChild(shell)
    const view = render(<MobileSheet title="Read only" onClose={() => {}}>Body</MobileSheet>, { container: shell })
    expect(shell).toHaveAttribute('inert')
    view.unmount()
    expect(shell).toHaveAttribute('inert')
    shell.remove()
  })

  it('uses independent title and body scroll while keeping header actions outside the scrollers', () => {
    const titleRules = css.match(/\.gideon-mobile-sheet-header h2\s*\{([^}]+)\}/)?.[1] ?? ''
    const bodyRules = css.match(/\.gideon-mobile-sheet-body\s*\{([^}]+)\}/)?.[1] ?? ''
    const actionRules = css.match(/\.gideon-mobile-sheet-actions\s*\{([^}]+)\}/)?.[1] ?? ''
    expect(titleRules).toMatch(/overflow:\s*auto/)
    expect(titleRules).toMatch(/overflow-wrap:\s*anywhere/)
    expect(titleRules).toMatch(/white-space:\s*normal/)
    expect(titleRules).toMatch(/max-height:/)
    expect(bodyRules).toMatch(/min-height:\s*0/)
    expect(bodyRules).toMatch(/overflow:\s*auto/)
    expect(actionRules).toMatch(/flex:\s*0 0 auto/)
    expect(css).not.toMatch(/\.gideon-mobile-sheet-body\s*\{[^}]*overflow-x:\s*hidden/)
  })
})
