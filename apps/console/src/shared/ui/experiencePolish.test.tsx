import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Button } from './Button'
import { DialogShell } from './dialog/DialogShell'

describe('shared action states', () => {
  it('keeps the action name while exposing a changing reason and preventing blocked clicks', () => {
    const act = vi.fn()
    const { rerender } = render(<Button disabled disabledReason="No changes to save" onClick={act}>Save</Button>)
    const button = screen.getByRole('button', { name: 'Save' })
    expect(button).toHaveAttribute('data-visual-state', 'disabled')
    expect(button).toHaveAttribute('aria-disabled', 'true')
    expect(document.getElementById(button.getAttribute('aria-describedby')!)).toHaveTextContent('No changes to save')
    fireEvent.click(button)
    expect(act).not.toHaveBeenCalled()

    rerender(<Button loading loadingLabel="Saving changes" onClick={act}>Save</Button>)
    expect(button).toHaveAttribute('data-visual-state', 'loading')
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(document.getElementById(button.getAttribute('aria-describedby')!)).toHaveTextContent('Saving changes')

    rerender(<Button onClick={act}>Save</Button>)
    expect(button).toHaveAttribute('data-visual-state', 'ready')
    expect(button).not.toHaveAttribute('aria-describedby')
    fireEvent.click(button)
    expect(act).toHaveBeenCalledTimes(1)
  })
})

describe('dialog keyboard operation', () => {
  it('leaves focused action buttons to their native Enter behavior', () => {
    const close = vi.fn()
    render(<DialogShell request={{ kind: 'confirm', title: 'Continue?' }} onClose={close} />)
    const cancel = screen.getByRole('button', { name: 'Cancel' })
    fireEvent.keyDown(cancel, { key: 'Enter' })
    expect(close).not.toHaveBeenCalled()
    fireEvent.click(cancel)
    expect(close).toHaveBeenCalledWith(false)
  })

  it('submits a prompt from its text field and keeps the footer reachable below the scroll region', () => {
    const close = vi.fn()
    render(<DialogShell request={{ kind: 'prompt', title: 'Rename', fields: [{ name: 'name', label: 'Name', required: true }] }} onClose={close} />)
    const input = screen.getByRole('textbox', { name: 'Name' })
    fireEvent.change(input, { target: { value: 'Gideon' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(close).toHaveBeenCalledWith({ name: 'Gideon' })
    const footer = screen.getByRole('button', { name: 'Save' }).closest('footer')!
    expect(footer.previousElementSibling).toHaveClass('overflow-y-auto')
  })
})
