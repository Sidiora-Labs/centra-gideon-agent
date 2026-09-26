import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { DialogShell } from './DialogShell'
import type { DialogResult } from './dialogStore'

afterEach(cleanup)

const longName = 'workspace-' + 'unbrokentext'.repeat(24)

describe('shared dialogs on a narrow viewport', () => {
  it('keeps long confirmation text and both actions reachable without clipping', () => {
    const results: DialogResult[] = []
    const confirmLabel = 'Apply ' + longName
    const cancelLabel = 'Keep ' + longName
    render(<DialogShell request={{ kind: 'confirm', title: longName, body: longName, confirmLabel, cancelLabel }}
      onClose={result => results.push(result)} />)

    const dialog = screen.getByRole('dialog', { name: longName })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveClass('max-h-[calc(100dvh-1rem)]', 'min-w-0')
    expect(dialog.parentElement).toHaveClass('overflow-y-auto', 'p-s')
    const body = document.getElementById(dialog.getAttribute('aria-describedby')!)!
    expect(body).toHaveTextContent(longName)
    expect(body).toHaveClass('break-words')
    const footer = dialog.querySelector('footer')!
    expect(footer.previousElementSibling).toHaveClass('overflow-y-auto', 'min-w-0')
    expect(footer).toHaveClass('grid-cols-1', 'sm:flex', 'shrink-0')
    for (const button of within(footer).getAllByRole('button')) {
      expect(button).toHaveClass('min-h-11', 'min-w-0', 'break-words')
    }
    fireEvent.click(within(footer).getByRole('button', { name: confirmLabel }))
    expect(results).toEqual([true])
  })

  it('wraps prompt labels and errors while preserving the text-field Enter action', () => {
    const results: DialogResult[] = []
    render(<DialogShell request={{
      kind: 'prompt', title: 'Rename source', body: longName,
      fields: [{ name: 'name', label: longName, required: true,
        validate: value => value.length < 3 ? longName : null }],
    }} onClose={result => results.push(result)} />)

    const dialog = screen.getByRole('dialog', { name: 'Rename source' })
    const input = within(dialog).getByRole('textbox', { name: longName })
    expect(input).toHaveAttribute('aria-required', 'true')
    expect(input).toHaveClass('min-w-0', 'max-w-full')
    expect(within(dialog).getByText(longName, { selector: 'label' })).toHaveClass('break-words')
    fireEvent.change(input, { target: { value: 'x' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(results).toEqual([])
    expect(within(dialog).getByRole('alert')).toHaveClass('break-words')
    fireEvent.change(input, { target: { value: 'source' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(results).toEqual([{ name: 'source' }])
  })

  it('keeps textarea and password fields usable within the scroll region', () => {
    const results: DialogResult[] = []
    render(<DialogShell request={{ kind: 'prompt', title: 'Save settings',
      fields: [
        { name: 'notes', type: 'textarea', label: longName, required: true },
        { name: 'secret', type: 'password', label: 'Access key', required: true },
      ],
    }} onClose={result => results.push(result)} />)
    const dialog = screen.getByRole('dialog', { name: 'Save settings' })
    const notes = within(dialog).getByRole('textbox', { name: longName })
    const secret = within(dialog).getByLabelText('Access key')
    expect(notes).toHaveClass('min-w-0', 'max-w-full')
    expect(secret).toHaveAttribute('type', 'password')
    expect(secret).toHaveClass('min-w-0', 'max-w-full')
    fireEvent.change(notes, { target: { value: 'Keep these details' } })
    fireEvent.change(secret, { target: { value: 'saved-credential' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(results).toEqual([{ notes: 'Keep these details', secret: 'saved-credential' }])
  })

  it('keeps danger cancellation available from the keyboard', () => {
    const results: DialogResult[] = []
    render(<DialogShell request={{ kind: 'confirm', tone: 'danger', title: longName }}
      onClose={result => results.push(result)} />)
    const dialog = screen.getByRole('alertdialog', { name: longName })
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' })
    expect(cancel).toHaveClass('min-h-11')
    fireEvent.keyDown(cancel, { key: 'Escape' })
    expect(results).toEqual([false])
  })
})
