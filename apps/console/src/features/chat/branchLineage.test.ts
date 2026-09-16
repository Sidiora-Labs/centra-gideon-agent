import { describe, it, expect } from 'vitest'
import { branchIndexOf, branchParentKey } from './branchLineage'
import { hydrateTurns, type ChatTurn, type HistMsg, userTurn, assistantTurn } from './chatTypes'


const visibleIndexOfLast = (msgs: HistMsg[], predicate: (m: HistMsg, i: number) => boolean): number => {
  const visible = msgs.filter((m) => m.role === 'user' || m.role === 'assistant')
  let found = -1
  visible.forEach((m, i) => { if (predicate(m, i)) found = i })
  return found
}

describe('branchIndexOf — the turn position is not the message index', () => {
  it('agrees with the turn index on a plain alternating transcript', () => {
    const msgs: HistMsg[] = [
      { role: 'user', content: 'one' },
      { role: 'assistant', content: 'first answer' },
      { role: 'user', content: 'two' },
      { role: 'assistant', content: 'second answer' },
    ]
    const turns = hydrateTurns(msgs)
    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    expect(turns.map((_, i) => branchIndexOf(turns, i))).toEqual([0, 1, 2, 3])
  })

  it('branches at the RIGHT message when consecutive assistant messages merged', () => {
    const msgs: HistMsg[] = [
      { role: 'user', content: 'analyse this' },
      { role: 'assistant', content: 'part one' },
      { role: 'assistant', content: 'part two' },
      { role: 'assistant', content: 'part three' },
      { role: 'user', content: 'now the other direction' },
      { role: 'assistant', content: 'ok' },
    ]
    const turns = hydrateTurns(msgs)
    expect(turns).toHaveLength(4)

    expect(branchIndexOf(turns, 1)).toBe(3)
    expect(branchIndexOf(turns, 1)).not.toBe(1)
    expect(branchIndexOf(turns, 2)).toBe(4)
    expect(branchIndexOf(turns, 3)).toBe(5)
    expect(branchIndexOf(turns, 3)).toBe(visibleIndexOfLast(msgs, (m) => m.content === 'ok'))
  })

  it('branches at the RIGHT message when loop re-injections were collapsed', () => {
    const msgs: HistMsg[] = [
      { role: 'user', content: 'fix the build' },
      { role: 'tool', content: 'Terminal', meta: { tool_call_id: 't1', done: true } },
      { role: 'user', content: 'fix the build' },
      { role: 'tool', content: 'Read', meta: { tool_call_id: 't2', done: true } },
      { role: 'assistant', content: 'done' },
      { role: 'user', content: 'thanks' },
      { role: 'assistant', content: 'welcome' },
    ]
    const turns = hydrateTurns(msgs)
    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    expect(branchIndexOf(turns, 2)).toBe(visibleIndexOfLast(msgs, (m) => m.content === 'thanks'))
    expect(branchIndexOf(turns, 3)).toBe(visibleIndexOfLast(msgs, (m) => m.content === 'welcome'))
    expect(branchIndexOf(turns, 1)).toBe(visibleIndexOfLast(msgs, (m) => m.content === 'done'))
  })

  it('a re-injection AND a merged answer compound — the naive index is 2 short', () => {
    const msgs: HistMsg[] = [
      { role: 'user', content: 'go' },
      { role: 'tool', content: 'Terminal', meta: { tool_call_id: 't1', done: true } },
      { role: 'user', content: 'go' },
      { role: 'assistant', content: 'step one' },
      { role: 'assistant', content: 'step two' },
      { role: 'user', content: 'next' },
      { role: 'assistant', content: 'sure' },
    ]
    const turns = hydrateTurns(msgs)
    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    const want = visibleIndexOfLast(msgs, (m) => m.content === 'sure')
    expect(want).toBe(5)
    expect(branchIndexOf(turns, 3)).toBe(want)
    expect(want - 3).toBe(2)
  })

  it('branching the SAME message twice yields the same coordinate both times', () => {
    const turns = hydrateTurns([
      { role: 'user', content: 'q' },
      { role: 'assistant', content: 'a1' },
      { role: 'assistant', content: 'a2' },
    ])
    expect(branchIndexOf(turns, 1)).toBe(2)
    expect(branchIndexOf(turns, 1)).toBe(branchIndexOf(turns, 1))
  })

  it('derives a coordinate for LIVE turns appended after a hydrated history', () => {
    const turns: ChatTurn[] = hydrateTurns([
      { role: 'user', content: 'old q' },
      { role: 'assistant', content: 'old a part 1' },
      { role: 'assistant', content: 'old a part 2' },
    ])
    expect(turns[1].visibleIndex).toBe(2)
    turns.push(userTurn('new q'))
    turns.push(assistantTurn('new a'))
    expect(turns[2].visibleIndex).toBeUndefined()
    expect(branchIndexOf(turns, 2)).toBe(3)
    expect(branchIndexOf(turns, 3)).toBe(4)
  })

  it('a tool-only assistant turn holds no message slot, so it does not consume one', () => {
    const turns: ChatTurn[] = hydrateTurns([
      { role: 'user', content: 'q' },
      { role: 'assistant', content: 'a' },
    ])
    turns.push({ role: 'assistant', segments: [{ kind: 'tool', id: 'x', tool: 'Read', done: true }] })
    turns.push(assistantTurn('later text'))
    expect(branchIndexOf(turns, 3)).toBe(2)
  })

  it('falls back to the turn index when nothing is stamped (today’s behaviour)', () => {
    const turns: ChatTurn[] = [userTurn('a'), assistantTurn('b'), userTurn('c')]
    expect(branchIndexOf(turns, 2)).toBe(2)
  })

  it('is safe on an out-of-range index', () => {
    expect(branchIndexOf([], 4)).toBe(4)
  })
})

describe('branchParentKey — forked_from is a HISTORY key, routes take the bare key', () => {
  it('strips the dashboard: namespace the backend persists', () => {
    expect(branchParentKey('dashboard:abc123')).toBe('abc123')
  })

  it('tolerates the dashboard_ filename form and an already-bare key', () => {
    expect(branchParentKey('dashboard_abc123')).toBe('abc123')
    expect(branchParentKey('abc123')).toBe('abc123')
  })

  it('is empty for a session that was never branched', () => {
    expect(branchParentKey('')).toBe('')
    expect(branchParentKey(null)).toBe('')
    expect(branchParentKey(undefined)).toBe('')
  })

  it('round-trips a branch-of-a-branch key (nesting adds no encoding)', () => {
    expect(branchParentKey('dashboard:mid-branch')).toBe('mid-branch')
  })
})
