import { createRef } from 'react'
import { fireEvent, render, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SessionMarkerRail } from './SessionMarkerRail'
import { ChatActivityPanel } from './ChatActivityPanel'
import type { ChatTurn } from './chatTypes'
import { createScrollToTurnHandler } from './scrollToTurn'
import { AppearanceProvider } from '../../app/shell/appearance'
import { ThemeProvider } from '../../app/shell/theme'

const turns: Pick<ChatTurn, 'role' | 'segments'>[] = [
  { role: 'user', segments: [{ kind: 'text', text: 'Run it' }] },
  { role: 'assistant', segments: [{ kind: 'tool', id: '1', tool: 'shell', done: true, ok: false }, { kind: 'error', text: 'Failed' }] },
  { role: 'assistant', segments: [{ kind: 'text', text: 'Finished' }] },
]

class TestHighlight {
  ranges: Range[]
  constructor(...ranges: Range[]) { this.ranges = ranges }
}

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
  const onJumpTo = createScrollToTurnHandler((index) => nodes[index])
  const result = render(<ThemeProvider><AppearanceProvider><SessionMarkerRail turns={turns} scrollRef={scrollRef}
    nodeOf={(index) => nodes[index]} onJumpTo={onJumpTo} showReturnToNewest={showReturnToNewest}
    onReturnToNewest={onReturnToNewest} /></AppearanceProvider></ThemeProvider>)
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
    expect(within(container).getByRole('button', { name: 'Jump to message 2, Assistant: tool, error, completion' })).toBeTruthy()
    expect(container.querySelectorAll('[data-mark="tool"]')).toHaveLength(1)
    expect(container.querySelectorAll('[data-mark="error"]')).toHaveLength(1)
    expect(container.querySelectorAll('[data-mark="completion"]')).toHaveLength(3)
  })

  it('supports the complete keyboard walkthrough', () => {
    const { container, nodes } = setup()
    const first = within(container).getByRole('button', { name: 'Jump to message 1, You: completion' })
    const second = within(container).getByRole('button', { name: 'Jump to message 2, Assistant: tool, error, completion' })
    const third = within(container).getByRole('button', { name: 'Jump to message 3, Assistant: completion' })

    fireEvent.click(first)
    expect(nodes[0].scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })

    first.focus()
    fireEvent.keyDown(first, { key: 'ArrowDown' })
    expect(document.activeElement).toBe(second)
    expect(nodes[1].scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })

    fireEvent.keyDown(second, { key: 'End' })
    expect(document.activeElement).toBe(third)
    expect(nodes[2].scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })

    fireEvent.keyDown(third, { key: 'Home' })
    expect(document.activeElement).toBe(first)

    fireEvent.keyDown(first, { key: 'ArrowUp' })
    expect(document.activeElement).toBe(first)
  })

  it('uses opaque, contrasting tones for current and historical marks', () => {
    const { container } = setup()
    const current = container.querySelector('[data-marker-tone="current"] > span')
    const history = container.querySelector('[data-marker-tone="history"] > span')

    expect(current?.className).toContain('bg-primary')
    expect(current?.className).toContain('ring-primary')
    expect(history?.className).toContain('bg-outline')
    expect(history?.className).not.toMatch(/\/\d+/)
  })

  it('keeps the retired Activity Index absent while the rail owns message jumps', () => {
    const onJumpTo = vi.fn()
    const scrollRef = createRef<HTMLDivElement>()
    const { getByRole, queryByRole } = render(<ThemeProvider><AppearanceProvider><>
      <ChatActivityPanel activity={{ files: [], links: [] }} onOpenFile={vi.fn()} />
      <SessionMarkerRail turns={turns} scrollRef={scrollRef} nodeOf={() => null} onJumpTo={onJumpTo}
        showReturnToNewest={false} onReturnToNewest={vi.fn()} />
    </></AppearanceProvider></ThemeProvider>)

    expect(queryByRole('tab', { name: 'Index' })).toBeNull()
    fireEvent.click(getByRole('button', { name: 'Jump to message 3, Assistant: completion' }))
    expect(onJumpTo.mock.calls).toEqual([[2]])
  })

  it('renders one accessible return-to-newest control only when needed', () => {
    const { container, onReturnToNewest, rerender } = setup()
    const control = within(container).getByRole('button', { name: 'Return to newest message' })
    expect(within(container).getAllByRole('button', { name: /newest message/i })).toHaveLength(1)
    fireEvent.click(control)
    expect(onReturnToNewest).toHaveBeenCalledTimes(1)

    const scrollRef = createRef<HTMLDivElement>()
    rerender(<ThemeProvider><AppearanceProvider><SessionMarkerRail turns={turns} scrollRef={scrollRef} nodeOf={() => null}
      onJumpTo={vi.fn()} showReturnToNewest={false} onReturnToNewest={onReturnToNewest} /></AppearanceProvider></ThemeProvider>)
    expect(within(container).queryByRole('button', { name: /newest message/i })).toBeNull()
  })

  it('opens the mobile drawer and closes it after a mark scrolls to its turn', () => {
    const { container, nodes } = setup(false)
    fireEvent.click(within(container).getByRole('button', { name: 'Open session map' }))
    const drawer = within(container).getByRole('dialog', { name: 'Session map drawer' })
    fireEvent.click(within(drawer).getByRole('button', { name: 'Jump to message 3, Assistant: completion' }))
    expect(nodes[2].scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
    expect(within(container).queryByRole('dialog', { name: 'Session map drawer' })).toBeNull()
  })

  it.each(['rail', 'drawer', 'transcript'] as const)('scrolls a %s result to its turn, highlights its match, and names its source', (source) => {
    const searchable = [
      { role: 'user' as const, segments: [{ kind: 'text' as const, text: 'Find the cobalt passage' }] },
      { role: 'assistant' as const, segments: [{ kind: 'text' as const, text: 'Nothing here' }] },
    ]
    const highlights = new Map<string, unknown>()
    Object.assign(window, { CSS: { highlights }, Highlight: TestHighlight })
    const scroller = document.createElement('div')
    document.body.appendChild(scroller)
    const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
    scrollRef.current = scroller
    const nodes = searchable.map((turn) => {
      const node = document.createElement('div')
      node.textContent = turn.segments[0].text
      node.scrollIntoView = vi.fn()
      return node
    })
    const onJumpTo = createScrollToTurnHandler((index) => nodes[index])
    const { container } = render(<ThemeProvider><AppearanceProvider><SessionMarkerRail turns={searchable} scrollRef={scrollRef}
      nodeOf={(index) => nodes[index]} onJumpTo={onJumpTo} showReturnToNewest={false} onReturnToNewest={vi.fn()}
      searchSource={source} /></AppearanceProvider></ThemeProvider>)

    fireEvent.click(within(container).getByRole('button', { name: 'Search session map' }))
    fireEvent.change(within(container).getByRole('searchbox', { name: 'Search this session' }), { target: { value: 'cobalt' } })
    const result = within(container).getByRole('listitem')
    expect(result.textContent).toContain(`Source: ${source}`)
    fireEvent.click(result)

    expect(nodes[0].scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
    const highlight = highlights.get('gideon-session-map') as TestHighlight
    expect(highlight.ranges.map((range) => range.toString())).toEqual(['cobalt'])
  })
})
