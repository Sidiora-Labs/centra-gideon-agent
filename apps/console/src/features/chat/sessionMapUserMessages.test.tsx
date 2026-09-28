import { createRef } from 'react'
import { render, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AppearanceProvider } from '../../app/shell/appearance'
import { ThemeProvider } from '../../app/shell/theme'
import type { ChatTurn } from './chatTypes'
import { SessionMarkerRail } from './SessionMarkerRail'
import { createScrollToTurnHandler } from './scrollToTurn'
import { sessionMapEntries, sessionMapResults } from './sessionMapSearch'

const turns: Pick<ChatTurn, 'role' | 'segments'>[] = [
  { role: 'assistant', segments: [{ kind: 'text', text: 'Resumed context' }] },
  { role: 'user', segments: [{ kind: 'text', text: 'Question one' }] },
  { role: 'assistant', segments: [{ kind: 'tool', id: '1', tool: 'shell', detail: 'npm run build', done: true }] },
  { role: 'assistant', segments: [{ kind: 'error', text: 'Build needs attention' }] },
  { role: 'assistant', segments: [{ kind: 'text', text: 'Build result' }] },
  { role: 'user', segments: [{ kind: 'text', text: 'Question two' }] },
  { role: 'assistant', segments: [{ kind: 'tool', id: '2', tool: 'shell', output: 'Finished checks', done: true }] },
  { role: 'assistant', segments: [{ kind: 'text', text: 'Second answer' }] },
]

describe('Session Map user question entries', () => {
  it('groups all result turns under their opening user question and searches through those coordinates', () => {
    const entries = sessionMapEntries(turns)
    expect(entries.map(({ index, turnIndex, endIndex }) => ({ index, turnIndex, endIndex }))).toEqual([
      { index: 0, turnIndex: 1, endIndex: 4 },
      { index: 1, turnIndex: 5, endIndex: 7 },
    ])
    expect(sessionMapResults(turns, 'npm run build')).toEqual([
      { index: 2, entryIndex: 0, text: 'shell · npm run build', source: 'Tool: shell' },
    ])
  })

  it('shows one marker per user question and announces the question index', () => {
    const scrollRef = createRef<HTMLDivElement>()
    const nodes = turns.map(() => document.createElement('div'))
    const onJumpTo = createScrollToTurnHandler((index) => nodes[index])
    const { container } = render(<ThemeProvider><AppearanceProvider>
      <SessionMarkerRail turns={turns} scrollRef={scrollRef} nodeOf={(index) => nodes[index]} onJumpTo={onJumpTo}
        showReturnToNewest={false} onReturnToNewest={() => onJumpTo(turns.length - 1)} />
    </AppearanceProvider></ThemeProvider>)

    const rail = within(container).getByRole('region', { name: 'Session map messages' })
    const markers = rail.querySelectorAll<HTMLButtonElement>('[data-session-marker]')
    expect(markers).toHaveLength(2)
    expect(markers[0]).toHaveAccessibleName('Jump to message 1, You: Question one')
    expect(markers[1]).toHaveAccessibleName('Jump to message 2, You: Question two')
    expect(within(rail).getByRole('status', { name: 'Session map position' })).toHaveTextContent('Message 1 of 2')
    expect([...markers].map((marker) => marker.tabIndex)).toEqual([0, -1])
  })
})
