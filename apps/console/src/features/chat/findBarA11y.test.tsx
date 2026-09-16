import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createRef } from 'react'
import { render, within, fireEvent, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FindBar, findAnnouncement } from '../../shared/ui/FindBar'
import { findSegments } from './findSegments'
import { FollowupChips, followupAnnouncement } from './FollowupChips'
import type { ChatTurn } from './chatTypes'


const turnOf = (text: string): ChatTurn => ({ role: 'user', segments: [{ kind: 'text', text }] })

function mountBar(text: string, onClose = () => {}) {
  const host = document.createElement('div')
  host.textContent = text
  document.body.appendChild(host)

  const opener = document.createElement('button')
  opener.textContent = 'composer'
  document.body.appendChild(opener)
  opener.focus()
  expect(document.activeElement).toBe(opener)

  const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
  scrollRef.current = host
  const utils = render(
    <FindBar items={[turnOf(text)]} segmentsOf={findSegments} nodeOf={() => null}
      scrollRef={scrollRef} label="Find in conversation" onClose={onClose} />,
  )
  return { ...utils, host, opener }
}

async function typeQuery(container: HTMLElement, query: string) {
  const input = within(container).getByLabelText('Find in conversation')
  fireEvent.change(input, { target: { value: query } })
  await act(async () => { await new Promise((r) => setTimeout(r, 200)) })
  return input
}

function announced(container: HTMLElement): string {
  return within(container).getByRole('status').textContent?.trim() ?? ''
}

describe('findAnnouncement — the wording, as a value', () => {
  it('is silent before a search (a live region must exist EMPTY, not absent)', () => {
    expect(findAnnouncement('', 0, 0)).toBe('')
    expect(findAnnouncement('   ', 0, 12)).toBe('')
  })

  it('words the zero case instead of "0/0"', () => {
    expect(findAnnouncement('nothing', 0, 0)).toBe('No matches')
  })

  it('words the position AND the total, so cycling re-announces', () => {
    expect(findAnnouncement('kiln', 0, 17)).toBe('Match 1 of 17')
    expect(findAnnouncement('kiln', 2, 17)).toBe('Match 3 of 17')
  })

  it('avoids the ResultAnnouncement noun trap ("No matching matches")', () => {
    expect(findAnnouncement('x', 0, 0)).not.toContain('matching matches')
  })
})

describe('FindBar aria-live is CONTENT, not an attribute', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('announces words when a query matches, and the glyph counter is hidden from AT', async () => {
    const { container } = mountBar('kiln target and target again')
    expect(announced(container)).toBe('')

    await typeQuery(container, 'target')
    await waitFor(() => expect(announced(container)).toBe('Match 1 of 1'))

    const glyph = container.querySelector('[aria-hidden="true"].tabular-nums')
    expect(glyph?.textContent).toBe('1/1')
  })

  it('announces "No matches" rather than "0/0"', async () => {
    const { container } = mountBar('kiln target')
    await typeQuery(container, 'zzzz')
    await waitFor(() => expect(announced(container)).toBe('No matches'))
    expect(announced(container)).not.toBe('0/0')
  })

  it('re-announces the position when the user cycles matches', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
    scrollRef.current = host
    const { container } = render(
      <FindBar items={[turnOf('target one'), turnOf('target two')]} segmentsOf={findSegments}
        nodeOf={() => null} scrollRef={scrollRef} label="Find in conversation" onClose={() => {}} />,
    )
    await typeQuery(container, 'target')
    await waitFor(() => expect(announced(container)).toBe('Match 1 of 2'))

    fireEvent.click(within(container).getByLabelText('Next match'))
    await waitFor(() => expect(announced(container)).toBe('Match 2 of 2'))
  })
})

describe('FindBar keyboard traversal is DRIVEN, not declared', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('reaches every control in visual order and leaves nothing unreachable', async () => {
    const { container } = mountBar('kiln target')
    const stops = Array.from(
      container.querySelectorAll<HTMLElement>('input, button:not([tabindex="-1"])'))
      .map((el) => el.getAttribute('aria-label') ?? el.tagName.toLowerCase())
    expect(stops).toEqual(['Find in conversation', 'Previous match', 'Next match', 'Close find'])
    for (const el of container.querySelectorAll<HTMLElement>('input, button')) {
      expect(el.tabIndex).toBeGreaterThanOrEqual(0)
    }
  })

  it('↑ / ↓ cycle from the field instead of moving the caret', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
    scrollRef.current = host
    const { container } = render(
      <FindBar items={[turnOf('target one'), turnOf('target two')]} segmentsOf={findSegments}
        nodeOf={() => null} scrollRef={scrollRef} label="Find in conversation" onClose={() => {}} />,
    )
    const input = await typeQuery(container, 'target')
    await waitFor(() => expect(announced(container)).toBe('Match 1 of 2'))

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    await waitFor(() => expect(announced(container)).toBe('Match 2 of 2'))
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    await waitFor(() => expect(announced(container)).toBe('Match 1 of 2'))
  })

  it('Enter / Shift+Enter cycle from the field', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
    scrollRef.current = host
    const { container } = render(
      <FindBar items={[turnOf('target one'), turnOf('target two')]} segmentsOf={findSegments}
        nodeOf={() => null} scrollRef={scrollRef} label="Find in conversation" onClose={() => {}} />,
    )
    const input = await typeQuery(container, 'target')
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(announced(container)).toBe('Match 2 of 2'))
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    await waitFor(() => expect(announced(container)).toBe('Match 1 of 2'))
  })

  it('Escape closes from EVERY tab stop, not just the field', async () => {
    for (const label of ['Find in conversation', 'Previous match', 'Next match', 'Close find']) {
      const onClose = vi.fn()
      const { container, unmount } = mountBar('kiln target', onClose)
      const el = within(container).getByLabelText(label)
      el.focus()
      fireEvent.keyDown(el, { key: 'Escape' })
      expect(onClose, `Escape from "${label}" did not close the bar`).toHaveBeenCalledTimes(1)
      unmount()
      document.body.innerHTML = ''
    }
  })

  it('hands focus back to whatever had it when the bar closes', async () => {
    const { unmount, opener } = mountBar('kiln target')
    await waitFor(() => expect(document.activeElement).not.toBe(opener))
    unmount()
    expect(document.activeElement).toBe(opener)
  })
})

describe('FindBar docks on mobile (measured at the useIsMobile breakpoint)', () => {
  const ORIGINAL = window.matchMedia
  function setViewport(mobile: boolean) {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true, writable: true,
      value: ((q: string) => ({
        matches: q === '(max-width: 768px)' ? mobile : false,
        media: q, onchange: null,
        addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
      })) as unknown as typeof window.matchMedia,
    })
  }
  beforeEach(() => { setViewport(false) })
  afterEach(() => {
    Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL })
    document.body.innerHTML = ''
  })

  it('is a right-aligned intrinsic-width pill on desktop', () => {
    setViewport(false)
    const { container } = mountBar('kiln')
    const bar = container.querySelector('[role="search"]')!
    expect(bar.className).toContain('w-fit')
    expect(bar.className).toContain('ml-auto')
    expect(bar.className).not.toContain('w-auto')
  })

  it('spans the column on mobile, so the pill cannot hang off a narrow viewport', () => {
    setViewport(true)
    const { container } = mountBar('kiln')
    const bar = container.querySelector('[role="search"]')!
    expect(bar.className).toContain('w-auto')
    expect(bar.className).not.toContain('w-fit')
    expect(bar.className).toContain('sticky')
    expect(bar.className).toContain('mx-l')
  })
})

describe('followupAnnouncement — chips arrival is spoken, and dismissal clears it', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('is empty with no chips, so a dismissal does not leave a stale claim', () => {
    expect(followupAnnouncement(0)).toBe('')
    expect(followupAnnouncement(-1)).toBe('')
  })

  it('counts and singularises', () => {
    expect(followupAnnouncement(1)).toBe('1 follow-up suggestion available')
    expect(followupAnnouncement(3)).toBe('3 follow-up suggestions available')
  })

  it('the chips themselves still carry a group name for whoever reaches them', () => {
    const { container } = render(
      <FollowupChips items={['a', 'b']} onPick={() => {}} onSend={() => {}} />)
    const group = within(container).getByRole('group', { name: 'Suggested follow-ups' })
    expect(within(group).getAllByRole('button')).toHaveLength(4)
  })

  it('every chip send glyph names WHICH chip it sends', () => {
    const { container } = render(
      <FollowupChips items={['draft the summary', 'run the tests']} onPick={() => {}} onSend={() => {}} />)
    expect(within(container).getByLabelText('Send: draft the summary')).toBeTruthy()
    expect(within(container).getByLabelText('Send: run the tests')).toBeTruthy()
  })
})

describe('FollowupChips keyboard traversal is DRIVEN, not declared', () => {
  afterEach(() => { document.body.innerHTML = '' })

  const focused = () => {
    const el = document.activeElement as HTMLElement | null
    if (!el || el === document.body) return 'body'
    return el.getAttribute('aria-label') ?? (el.textContent || el.tagName.toLowerCase())
  }

  it('Tab reaches both halves of every chip, in visual order', async () => {
    const user = userEvent.setup()
    render(<FollowupChips items={['draft the summary', 'run the tests']}
      onPick={() => {}} onSend={() => {}} />)

    const stops: string[] = []
    for (let i = 0; i < 4; i++) { await user.tab(); stops.push(focused()) }
    expect(stops).toEqual([
      'draft the summary', 'Send: draft the summary',
      'run the tests', 'Send: run the tests',
    ])
    expect(stops).not.toContain('body')
  })

  it('Enter on a chip label fills the composer WITHOUT sending it', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    const onSend = vi.fn()
    render(<FollowupChips items={['draft the summary']} onPick={onPick} onSend={onSend} />)

    await user.tab()
    expect(focused()).toBe('draft the summary')
    await user.keyboard('{Enter}')
    expect(onPick).toHaveBeenCalledWith('draft the summary')
    expect(onSend).not.toHaveBeenCalled()
  })

  it('Enter on the send glyph sends, so double-click is not the only way', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    const onSend = vi.fn()
    render(<FollowupChips items={['draft the summary', 'run the tests']}
      onPick={onPick} onSend={onSend} />)

    for (let i = 0; i < 4; i++) await user.tab()
    expect(focused()).toBe('Send: run the tests')
    await user.keyboard('{Enter}')
    expect(onSend).toHaveBeenCalledWith('run the tests')
    expect(onSend).toHaveBeenCalledTimes(1)
    expect(onPick).not.toHaveBeenCalled()
  })
})
