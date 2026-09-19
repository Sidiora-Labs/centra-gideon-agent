import { createRef } from 'react'
import { fireEvent, render, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SessionMarkerRail } from './SessionMarkerRail'
import type { ChatTurn } from './chatTypes'

const turns: Pick<ChatTurn, 'role'>[] = [
  { role: 'user' },
  { role: 'assistant' },
  { role: 'user' },
]

function setup(showReturnToNewest = true) {
  const scroller = document.createElement('div')
  Object.defineProperties(scroller, {
    clientHeight: { configurable: true, value: 200 },
    scrollHeight: { configurable: true, value: 800 },
    scrollTop: { configurable: true, writable: true, value: 200 },
  })
  document.body.appendChild(scroller)
  const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
  scrollRef.current = scroller
  const nodes = turns.map((_, index) => {
    const node = document.createElement('div')
    Object.defineProperty(node, 'offsetTop', { configurable: true, value: index * 300 })
    node.scrollIntoView = vi.fn()
    return node
  })
  const onReturnToNewest = vi.fn()
  const result = render(<SessionMarkerRail turns={turns} scrollRef={scrollRef}
    nodeOf={(index) => nodes[index]} showReturnToNewest={showReturnToNewest}
    onReturnToNewest={onReturnToNewest} />)
  return { ...result, scroller, nodes, onReturnToNewest }
}

describe('SessionMarkerRail', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('exposes a named Session map region and visualizes the viewport', () => {
    const { container } = setup()
    expect(within(container).getByRole('region', { name: 'Session map messages' })).toBeTruthy()
    const viewport = within(container).getByTestId('session-map-viewport')
    expect(viewport.style.top).toBe('25%')
    expect(viewport.style.height).toBe('25%')
  })

  it('gives each marker a larger target and an accessible jump name', () => {
    const { container } = setup()
    const markers = container.querySelectorAll<HTMLButtonElement>('[data-session-marker]')
    expect(markers).toHaveLength(3)
    expect(markers[0].className).toContain('size-8')
    expect(within(container).getByRole('button', { name: 'Jump to message 2, Assistant' })).toBeTruthy()
  })

  it('jumps with click and arrow-key navigation', () => {
    const { container, nodes } = setup()
    const first = within(container).getByRole('button', { name: 'Jump to message 1, You' })
    const second = within(container).getByRole('button', { name: 'Jump to message 2, Assistant' })

    fireEvent.click(first)
    expect(nodes[0].scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })

    first.focus()
    fireEvent.keyDown(first, { key: 'ArrowDown' })
    expect(document.activeElement).toBe(second)
    expect(nodes[1].scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
  })

  it('renders one accessible return-to-newest control only when needed', () => {
    const { container, onReturnToNewest, rerender } = setup()
    const control = within(container).getByRole('button', { name: 'Return to newest message' })
    expect(within(container).getAllByRole('button', { name: /newest message/i })).toHaveLength(1)
    fireEvent.click(control)
    expect(onReturnToNewest).toHaveBeenCalledTimes(1)

    const scrollRef = createRef<HTMLDivElement>()
    rerender(<SessionMarkerRail turns={turns} scrollRef={scrollRef} nodeOf={() => null}
      showReturnToNewest={false} onReturnToNewest={onReturnToNewest} />)
    expect(within(container).queryByRole('button', { name: /newest message/i })).toBeNull()
  })
})
