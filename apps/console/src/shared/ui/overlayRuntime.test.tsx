import { afterEach, describe, expect, it } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { DialogHost } from './dialog/DialogHost'
import { alertDialog, closeDialog, confirm, getDialogs, openDialog, promptForm, promptInput, subscribeDialogs, type DialogResult } from './dialog/dialogStore'
import { Modal } from './Modal'
import { MenuRow, Popover } from './Popover'
import { popupPosition, popupSide } from './popupController'

afterEach(() => {
  cleanup()
  for (const request of getDialogs()) closeDialog(request.id, null)
})

describe('dialog queue and result contracts', () => {
  it('publishes ordered snapshots and resolves each request once, including out-of-order closure', async () => {
    const observed: string[][] = []
    const unsubscribe = subscribeDialogs((dialogs) => observed.push(dialogs.map((dialog) => dialog.title)))
    const first = openDialog({ kind: 'confirm', title: 'First' })
    const second = openDialog({ kind: 'prompt', title: 'Second' })
    const [a, b] = getDialogs()
    expect(a.id).not.toBe(b.id)
    closeDialog(a.id, true)
    closeDialog(a.id, false)
    closeDialog(b.id, { value: 'answer' })
    expect(await first).toBe(true)
    expect(await second).toEqual({ value: 'answer' })
    expect(observed).toEqual([[], ['First'], ['First', 'Second'], ['Second'], []])
    unsubscribe()
  })

  it('preserves typed convenience results and the reserved form value field', async () => {
    const accepted = confirm({ title: 'Proceed', danger: true })
    expect(getDialogs()[0]).toMatchObject({ tone: 'danger', kind: 'confirm' })
    closeDialog(getDialogs()[0].id, { value: 'truthy is not approval' })
    expect(await accepted).toBe(false)
    const input = promptInput('Name')
    expect(getDialogs()[0].fields?.[0].required).toBe(true)
    closeDialog(getDialogs()[0].id, { value: '  raw  ' })
    expect(await input).toBe('  raw  ')
    const form = promptForm({ title: 'Details', fields: [] })
    closeDialog(getDialogs()[0].id, { value: 'reserved', name: 'Kept' })
    expect(await form).toEqual({ name: 'Kept' })
    const notice = alertDialog('Notice')
    closeDialog(getDialogs()[0].id, false)
    expect(await notice).toBeUndefined()
  })

  it('supports a subscriber opening the next dialog during a close notification', async () => {
    const first = openDialog({ kind: 'confirm', title: 'First' })
    let next: Promise<DialogResult> | undefined
    const unsubscribe = subscribeDialogs((dialogs) => {
      if (dialogs.length === 0 && !next) next = openDialog({ kind: 'alert', title: 'Next' })
    })
    closeDialog(getDialogs()[0].id, true)
    expect(getDialogs().map((dialog) => dialog.title)).toEqual(['Next'])
    unsubscribe()
    closeDialog(getDialogs()[0].id, true)
    expect(await first).toBe(true)
    expect(await next).toBe(true)
  })
})

describe('dialog keyboard and validation ownership', () => {
  it('validates required and custom fields, clears errors on edit, and returns untrimmed input', async () => {
    render(<DialogHost />)
    let result!: Promise<string | null>
    act(() => { result = promptInput({ title: 'Project', label: 'Project name', validate: (value) => value.includes('/') ? 'No slash' : null }) })
    const input = screen.getByLabelText('Project name')
    expect(document.activeElement).toBe(input)
    expect(screen.getByRole('button', { name: 'Save' })).toHaveAttribute('aria-disabled', 'true')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByRole('alert')).toHaveTextContent('Required')
    fireEvent.change(input, { target: { value: 'bad/name' } })
    expect(screen.queryByRole('alert')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByRole('alert')).toHaveTextContent('No slash')
    fireEvent.change(input, { target: { value: '  Project A  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(await result).toBe('  Project A  ')
    expect(getDialogs()).toHaveLength(0)
  })

  it('leaves Enter in multiline input and resolves prompt cancellation as null', async () => {
    render(<DialogHost />)
    let result!: Promise<string | null>
    act(() => { result = promptInput({ title: 'Notes', label: 'Notes text', type: 'textarea', initial: 'keep' }) })
    const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
    act(() => { screen.getByLabelText('Notes text').dispatchEvent(event) })
    expect(event.defaultPrevented).toBe(false)
    expect(getDialogs()).toHaveLength(1)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(await result).toBeNull()
  })

  it('focuses Cancel for dangerous confirmation and requires an explicit confirmation action', async () => {
    render(<DialogHost />)
    let result!: Promise<boolean>
    act(() => { result = confirm({ title: 'Remove project', danger: true, confirmLabel: 'Remove' }) })
    const dialog = screen.getByRole('alertdialog', { name: 'Remove project' })
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Cancel' }))
    fireEvent.keyDown(document.activeElement!, { key: 'Enter' })
    expect(getDialogs()).toHaveLength(1)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }))
    expect(await result).toBe(true)
  })

  it('dismisses one stacked dialog per Escape, including during exit animation', async () => {
    render(<DialogHost />)
    let first!: Promise<boolean>
    let second!: Promise<boolean>
    act(() => { first = confirm('First') })
    act(() => { second = confirm('Second') })
    expect(screen.getByRole('dialog', { name: 'Second' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'First' })).toBeNull()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(await second).toBe(false)
    expect(getDialogs().map((dialog) => dialog.title)).toEqual(['First'])
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(await first).toBe(false)
    expect(getDialogs()).toHaveLength(0)
  })

  it('keeps the underlying modal open when a dialog owns Escape', async () => {
    const dismissed: string[] = []
    render(<><Modal title="Workspace" onClose={() => dismissed.push('modal')}><button>Workspace action</button></Modal><DialogHost /></>)
    let result!: Promise<boolean>
    act(() => { result = confirm('Continue') })
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(await result).toBe(false)
    expect(dismissed).toEqual([])
    expect(screen.getByRole('dialog', { name: 'Workspace' })).toBeInTheDocument()
  })
})

describe('popup interaction ownership', () => {
  it('lets the most recently opened nested popup own Escape and restore its invoker', () => {
    render(<Popover portal trigger={(open, toggle) => <button data-open={open} onClick={toggle}>Outer</button>}>
      {() => <Popover trigger={(open, toggle) => <button data-open={open} onClick={toggle}>Inner</button>}>
        {() => <button>Nested action</button>}
      </Popover>}
    </Popover>)
    fireEvent.click(screen.getByText('Outer'))
    fireEvent.click(screen.getByText('Inner'))
    fireEvent.keyDown(screen.getByText('Nested action'), { key: 'Escape' })
    expect(screen.getByText('Inner')).toHaveAttribute('data-open', 'false')
    expect(screen.getByText('Outer')).toHaveAttribute('data-open', 'true')
    expect(document.activeElement).toBe(screen.getByText('Inner'))
    fireEvent.keyDown(screen.getByText('Inner'), { key: 'Escape' })
    expect(screen.getByText('Outer')).toHaveAttribute('data-open', 'false')
  })

  it('ignores the initial open signal, opens on changes, and closes on captured scroll without moving focus', () => {
    const view = (signal: number) => <><Popover portal openSignal={signal} trigger={(open, toggle) => <button data-open={open} onClick={toggle}>Open signal</button>}>
      {() => <button>Signal action</button>}
    </Popover><button>Outside</button></>
    const { rerender } = render(view(1))
    expect(screen.getByText('Open signal')).toHaveAttribute('data-open', 'false')
    rerender(view(2))
    expect(screen.getByText('Open signal')).toHaveAttribute('data-open', 'true')
    screen.getByText('Outside').focus()
    fireEvent.scroll(window)
    expect(screen.getByText('Open signal')).toHaveAttribute('data-open', 'false')
    expect(document.activeElement).toBe(screen.getByText('Outside'))
  })

  it('exposes selected radio and option states while keeping unavailable rows inert', () => {
    const actions: string[] = []
    render(<><MenuRow role="menuitemradio" selected label="Chosen" onClick={() => actions.push('chosen')} />
      <MenuRow role="option" selected disabled label="Unavailable" onClick={() => actions.push('unavailable')} /></>)
    expect(screen.getByRole('menuitemradio')).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('option')).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('option'))
    fireEvent.click(screen.getByRole('menuitemradio'))
    expect(actions).toEqual(['chosen'])
  })

  it('flips toward available space and clamps horizontal placement to the viewport', () => {
    const anchor = new DOMRect(window.innerWidth - 30, 20, 20, 20)
    expect(popupSide(anchor, 'top', window.innerHeight)).toBe('bottom')
    expect(popupPosition(anchor, 'bottom', 'left', 200)).toEqual({ left: window.innerWidth - 208, top: 46 })
    expect(popupPosition(new DOMRect(2, 200, 10, 20), 'top', 'right', 200)).toEqual({ left: 8, bottom: window.innerHeight - 194 })
  })
})
