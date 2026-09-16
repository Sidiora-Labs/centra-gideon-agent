import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { PanelLeft } from 'lucide-react'
import { SpotlightTour } from './SpotlightTour'


const STEPS = [
  { id: 'one', anchor: 'a-one', icon: PanelLeft, title: 'First stop', body: 'Body one.' },
  { id: 'two', anchor: 'a-two', icon: PanelLeft, title: 'Second stop', body: 'Body two.' },
]

function Harness({ onExit, withLayer }: { onExit: () => void; withLayer: boolean }) {
  const [index, setIndex] = useState(0)
  return (
    <>
      <div data-tour="a-one">anchor one</div>
      {withLayer && <input aria-label="Search pages and actions" defaultValue="" />}
      <SpotlightTour steps={STEPS} index={index} label="Gideon tour"
        onIndex={setIndex} onExit={onExit} />
    </>
  )
}

describe('Escape goes to the focused layer', () => {
  it('exits the tour when the tour holds focus', async () => {
    const onExit = vi.fn()
    const user = userEvent.setup()
    render(<Harness onExit={onExit} withLayer={false} />)
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    await user.keyboard('{Escape}')
    expect(onExit).toHaveBeenCalledTimes(1)
  })

  it('exits when nothing at all holds focus, so it never becomes undismissable', async () => {
    const onExit = vi.fn()
    const user = userEvent.setup()
    render(<Harness onExit={onExit} withLayer={false} />)
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    ;(document.activeElement as HTMLElement | null)?.blur()
    expect(document.activeElement === document.body || document.activeElement === null).toBe(true)
    await user.keyboard('{Escape}')
    expect(onExit).toHaveBeenCalledTimes(1)
  })

  it('does NOT exit when a layer above it holds focus — the defect this pins', async () => {
    const onExit = vi.fn()
    const user = userEvent.setup()
    render(<Harness onExit={onExit} withLayer />)
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    const above = screen.getByLabelText('Search pages and actions')
    above.focus()
    expect(document.activeElement).toBe(above)
    await user.keyboard('{Escape}')
    expect(onExit, 'the tour must not eat a key pressed at a layer above it').not.toHaveBeenCalled()
  })

  it('still lets the tour go once focus comes back to it', async () => {
    const onExit = vi.fn()
    const user = userEvent.setup()
    render(<Harness onExit={onExit} withLayer />)
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    const above = screen.getByLabelText('Search pages and actions')
    above.focus()
    await user.keyboard('{Escape}')
    expect(onExit).not.toHaveBeenCalled()
    screen.getByRole('dialog').focus()
    await user.keyboard('{Escape}')
    expect(onExit).toHaveBeenCalledTimes(1)
  })

  it('the X button and a shield click still exit regardless of focus', async () => {
    const onExit = vi.fn()
    const user = userEvent.setup()
    render(<Harness onExit={onExit} withLayer />)
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    screen.getByLabelText('Search pages and actions').focus()
    await user.click(screen.getByRole('button', { name: 'End the tour' }))
    expect(onExit).toHaveBeenCalledTimes(1)
  })
})
