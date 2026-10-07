import { useState } from "react"
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AssistantModal } from '../../../vendor/assistant-ui/elements/assistant-modal.aui'

describe('source-derived controlled assistant modal', () => {
  it('shows a caller-owned conversation and authenticated history without a duplicate trigger', () => {
    const changes: boolean[] = []
    const { rerender } = render(<AssistantModal open trigger={null} onOpenChange={next => changes.push(next)}
      thread={<div data-testid="connected-thread">Live Gideon chat</div>}
      history={<div data-testid="session-history">Owned session preview</div>} />)
    expect(screen.getByRole('dialog', { name: 'Conversation' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open assistant' })).toBeNull()
    expect(screen.getAllByTestId('connected-thread')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    expect(screen.getByTestId('session-history')).toHaveTextContent('Owned session preview')
    expect(screen.queryByTestId('connected-thread')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Close assistant' }))
    expect(changes).toEqual([false])
    rerender(<AssistantModal open={false} trigger={null} thread={<div>Live Gideon chat</div>} history={<div>Owned session preview</div>} />)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})


it('dismisses the internal assistant with Escape and restores the real caller opener', () => {
  function Host() {
    const [open, setOpen] = useState(false)
    return <>
      <button type="button" onClick={() => setOpen(true)}>Open conversation panel</button>
      <AssistantModal open={open} trigger={null} onOpenChange={setOpen}
        thread={<p>Current conversation</p>} history={<p>Saved sessions</p>} />
    </>
  }
  render(<Host />)
  const opener = screen.getByRole('button', { name: 'Open conversation panel' })
  opener.focus()
  fireEvent.click(opener)
  expect(screen.getByRole('dialog', { name: 'Conversation' })).toHaveAttribute('aria-modal', 'true')
  expect(screen.getByRole('button', { name: 'Conversation' })).toHaveFocus()
  fireEvent.keyDown(window, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(opener).toHaveFocus()
})
