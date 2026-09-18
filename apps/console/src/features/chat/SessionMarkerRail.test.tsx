import { describe, it, expect, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen, fireEvent } from '@testing-library/react'
import { RAIL_MIN_MARKERS, SessionMarkerRail } from './SessionMarkerRail'
import { deriveSessionMarkers, type SessionMarker } from './sessionMarkers'
import { hydrateTurns, type HistMsg } from './chatTypes'

const marker = (over: Partial<SessionMarker> = {}): SessionMarker => ({
  id: 't0', kind: 'turn', label: 'hello', role: 'user', turnIndex: 0, jumpIndex: 0, failedTool: false, ...over,
})

const many = (n: number): SessionMarker[] =>
  Array.from({ length: n }, (_, i) => marker({ id: `t${i}`, label: `turn ${i}`, turnIndex: i, jumpIndex: i }))

const rail = () => screen.queryByRole('navigation', { name: 'Session index' })
const railButtons = () => screen.queryAllByRole('button')

describe('SessionMarkerRail — one marker per derived event', () => {
  it('renders a button per marker inside a labelled vertical index', () => {
    render(<SessionMarkerRail markers={many(6)} currentTurn={5} onJump={() => {}} />)
    expect(rail()).not.toBeNull()
    expect(railButtons()).toHaveLength(6)
    expect(screen.getByRole('button', { name: 'You: turn 3' })).toBeTruthy()
  })

  it('renders one marker per event of a real transcript, in order', () => {
    const msgs: HistMsg[] = [
      { role: 'user', content: 'delegate this' },
      { role: 'tool', content: 'Task', meta: { tool_call_id: 't1', tool: 'Task', done: true } },
      { role: 'permission', content: 'Terminal', meta: { approval_id: 'a1', tool: 'Terminal' } },
      { role: 'assistant', content: 'done' },
    ]
    const markers = deriveSessionMarkers(hydrateTurns(msgs))
    render(<SessionMarkerRail markers={markers} currentTurn={1} onJump={() => {}} />)
    const names = railButtons().map((b) => b.getAttribute('aria-label'))
    expect(names).toEqual(['You: delegate this', 'Assistant: done', 'Subagent: Task', 'Approval: Terminal'])
    expect(railButtons().map((b) => b.getAttribute('data-kind'))).toEqual(['turn', 'turn', 'subagent', 'approval'])
  })

  it('names a failed tool in its accessible label', () => {
    render(<SessionMarkerRail markers={[...many(3), marker({ id: 'x', kind: 'tool', label: 'Terminal', role: 'assistant', turnIndex: 3, failedTool: true })]}
      currentTurn={0} onJump={() => {}} />)
    expect(screen.getByRole('button', { name: 'Tool: Terminal (failed tool)' })).toBeTruthy()
  })
})

describe('SessionMarkerRail — suppressed when there is nothing to navigate', () => {
  it('renders nothing below the threshold', () => {
    for (let n = 0; n < RAIL_MIN_MARKERS; n++) {
      const { unmount } = render(<SessionMarkerRail markers={many(n)} currentTurn={0} onJump={() => {}} />)
      expect(rail()).toBeNull()
      expect(railButtons()).toHaveLength(0)
      unmount()
    }
  })

  it('appears as soon as the threshold is reached', () => {
    render(<SessionMarkerRail markers={many(RAIL_MIN_MARKERS)} currentTurn={0} onJump={() => {}} />)
    expect(rail()).not.toBeNull()
    expect(railButtons()).toHaveLength(RAIL_MIN_MARKERS)
  })
})

describe('SessionMarkerRail — current vs historical', () => {
  it('marks only the current turn’s markers, by aria-current and by class', () => {
    const markers = [...many(4), marker({ id: 't3s0', kind: 'tool', label: 'Read', role: 'assistant', turnIndex: 3, jumpIndex: 3 })]
    render(<SessionMarkerRail markers={markers} currentTurn={3} onJump={() => {}} />)
    const buttons = railButtons()
    expect(buttons.map((b) => b.getAttribute('data-state')))
      .toEqual(['historical', 'historical', 'historical', 'current', 'current'])
    expect(buttons.filter((b) => b.getAttribute('aria-current') === 'true')).toHaveLength(2)
    const [historical, current] = [buttons[0], buttons[3]]
    expect(current.className).not.toBe(historical.className)
    expect(current.className).toContain('bg-primary')
    expect(historical.className).not.toContain('bg-primary')
  })

  it('styles a historical failed-tool marker apart from a plain historical one', () => {
    const markers = [...many(3), marker({ id: 'f', kind: 'tool', label: 'Terminal', role: 'assistant', turnIndex: 9, jumpIndex: 9, failedTool: true })]
    render(<SessionMarkerRail markers={markers} currentTurn={0} onJump={() => {}} />)
    const buttons = railButtons()
    expect(buttons[3].className).toContain('bg-danger')
    expect(buttons[1].className).not.toContain('bg-danger')
  })
})

describe('SessionMarkerRail — clicking jumps to the turn', () => {
  it('reports the clicked marker, carrying its turn and fork coordinate', () => {
    const onJump = vi.fn()
    const markers = deriveSessionMarkers(hydrateTurns([
      { role: 'user', content: 'q one' },
      { role: 'assistant', content: 'part one' },
      { role: 'assistant', content: 'part two' },
      { role: 'user', content: 'q two' },
      { role: 'assistant', content: 'a two' },
    ]))
    render(<SessionMarkerRail markers={markers} currentTurn={3} onJump={onJump} />)
    fireEvent.click(screen.getByRole('button', { name: 'Assistant: part one part two' }))
    expect(onJump).toHaveBeenCalledTimes(1)
    expect(onJump.mock.calls[0][0]).toMatchObject({ turnIndex: 1, jumpIndex: 2 })
  })

  it('jumps from any marker of a turn, event markers included', () => {
    const onJump = vi.fn()
    const markers = deriveSessionMarkers(hydrateTurns([
      { role: 'user', content: 'go' },
      { role: 'tool', content: 'Read', meta: { tool_call_id: 't1', done: true } },
      { role: 'assistant', content: 'read it' },
      { role: 'user', content: 'again' },
      { role: 'assistant', content: 'ok' },
    ]))
    render(<SessionMarkerRail markers={markers} currentTurn={3} onJump={onJump} />)
    fireEvent.click(screen.getByRole('button', { name: 'Tool: Read' }))
    expect(onJump.mock.calls[0][0]).toMatchObject({ kind: 'tool', turnIndex: 1, jumpIndex: 1 })
  })
})

describe('SessionMarkerRail — mounted in the chat surface', () => {
  const page = readFileSync(join(process.cwd(), 'src/features/ChatPage.tsx'), 'utf8')

  it('is rendered from the derived markers and jumps through the page’s turn scroller', () => {
    expect(page).toContain("import { deriveSessionMarkers } from './chat/sessionMarkers'")
    expect(page).toContain('const sessionMarkers = useMemo(() => deriveSessionMarkers(turns), [turns])')
    expect(page).toMatch(/<SessionMarkerRail markers=\{sessionMarkers\} currentTurn=\{turns\.length - 1\}/)
    expect(page).toContain('onJump={(m) => jumpToTurn(m.turnIndex)}')
  })
})
