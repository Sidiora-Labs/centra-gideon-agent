import { describe, expect, it } from 'vitest'
import { sessionMapResults, turnLabel } from './sessionMapSearch'
import type { ChatTurn } from './chatTypes'

describe('session map navigation', () => {
  const turns: ChatTurn[] = [
    { role: 'user', segments: [{ kind: 'text', text: 'Find the release brief for September' }] },
    { role: 'assistant', segments: [
      { kind: 'text', text: 'I found the brief.' },
      { kind: 'tool', id: 'read-1', tool: 'read_file', output: 'September release milestones', done: true },
    ] },
  ]

  it('provides short labels and identifies where a match came from', () => {
    expect(turnLabel(turns[0])).toBe('Find the release brief for September')
    expect(sessionMapResults(turns, 'brief')).toEqual([
      { index: 0, text: 'Find the release brief for September', source: 'Your message' },
      { index: 1, text: 'I found the brief.', source: 'Agent response' },
    ])
    expect(sessionMapResults(turns, 'milestones')).toEqual([
      { index: 1, text: 'read_file · September release milestones', source: 'Tool: read_file' },
    ])
  })
})
