import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { branchIndexOf } from './branchLineage'
import { deriveSessionMarkers, LABEL_MAX } from './sessionMarkers'
import { assistantTurn, hydrateTurns, userTurn, type ChatTurn, type HistMsg } from './chatTypes'

const kinds = (turns: ChatTurn[]) => deriveSessionMarkers(turns).map((m) => m.kind)
const labels = (turns: ChatTurn[]) => deriveSessionMarkers(turns).map((m) => m.label)

describe('deriveSessionMarkers — one marker per turn', () => {
  it('yields exactly one marker per turn for a turn-only session', () => {
    const turns = hydrateTurns([
      { role: 'user', content: 'one' },
      { role: 'assistant', content: 'first answer' },
      { role: 'user', content: 'two' },
      { role: 'assistant', content: 'second answer' },
    ])
    const markers = deriveSessionMarkers(turns)
    expect(markers).toHaveLength(4)
    expect(markers.map((m) => m.kind)).toEqual(['turn', 'turn', 'turn', 'turn'])
    expect(markers.map((m) => m.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    expect(markers.map((m) => m.label)).toEqual(['one', 'first answer', 'two', 'second answer'])
    expect(markers.map((m) => m.turnIndex)).toEqual([0, 1, 2, 3])
    expect(markers.every((m) => m.failedTool === false)).toBe(true)
  })

  it('labels an empty turn by its side rather than inventing text', () => {
    const turns: ChatTurn[] = [userTurn(''), assistantTurn('')]
    expect(labels(turns)).toEqual(['You', 'Assistant'])
  })

  it('collapses whitespace and caps a long label', () => {
    const turns: ChatTurn[] = [userTurn(`please   fix\nthe ${'x'.repeat(200)}`)]
    const [marker] = deriveSessionMarkers(turns)
    expect(marker.label.length).toBe(LABEL_MAX)
    expect(marker.label.startsWith('please fix the ')).toBe(true)
  })

  it('is empty for an empty session', () => {
    expect(deriveSessionMarkers([])).toEqual([])
  })
})

describe('deriveSessionMarkers — every marker carries the owning turn’s jump coordinate', () => {
  it('uses the SAME coordinate forking uses when assistant messages merged', () => {
    const turns = hydrateTurns([
      { role: 'user', content: 'analyse this' },
      { role: 'assistant', content: 'part one' },
      { role: 'assistant', content: 'part two' },
      { role: 'assistant', content: 'part three' },
      { role: 'user', content: 'now the other way' },
      { role: 'assistant', content: 'ok' },
    ])
    const markers = deriveSessionMarkers(turns)
    expect(markers.map((m) => m.jumpIndex)).toEqual([0, 3, 4, 5])
    for (const m of markers) expect(m.jumpIndex).toBe(branchIndexOf(turns, m.turnIndex))
  })

  it('gives every event marker in a turn the turn’s coordinate, not its own position', () => {
    const turns = hydrateTurns([
      { role: 'user', content: 'go' },
      { role: 'tool', content: 'Terminal', meta: { tool_call_id: 't1', done: true } },
      { role: 'tool', content: 'Read', meta: { tool_call_id: 't2', done: true } },
      { role: 'assistant', content: 'done' },
    ])
    const markers = deriveSessionMarkers(turns)
    expect(markers.map((m) => m.kind)).toEqual(['turn', 'turn', 'tool', 'tool'])
    expect(markers.map((m) => m.jumpIndex)).toEqual([0, 1, 1, 1])
    expect(markers[2].turnIndex).toBe(1)
  })

  it('carries the live coordinate for turns appended after a hydrated history', () => {
    const turns = hydrateTurns([
      { role: 'user', content: 'old q' },
      { role: 'assistant', content: 'old a part 1' },
      { role: 'assistant', content: 'old a part 2' },
    ])
    turns.push(userTurn('new q'))
    turns.push(assistantTurn('new a'))
    expect(deriveSessionMarkers(turns).map((m) => m.jumpIndex)).toEqual([0, 2, 3, 4])
  })
})

describe('deriveSessionMarkers — failed tool state', () => {
  const failed: HistMsg[] = [
    { role: 'user', content: 'build it' },
    { role: 'tool', content: 'Terminal', meta: { tool_call_id: 't1', done: true, ok: false } },
    { role: 'assistant', content: 'the build broke' },
    { role: 'user', content: 'and again' },
    { role: 'tool', content: 'Terminal', meta: { tool_call_id: 't2', done: true } },
    { role: 'assistant', content: 'fine now' },
  ]

  it('flags the failing turn and leaves the healthy one clean', () => {
    const turns = hydrateTurns(failed)
    const markers = deriveSessionMarkers(turns)
    const byTurn = new Map(markers.map((m) => [m.id, m]))
    expect(markers.filter((m) => m.failedTool).map((m) => m.id)).toEqual(['t1', 't1s0'])
    expect(byTurn.get('t1')!.kind).toBe('turn')
    expect(byTurn.get('t1s0')!.kind).toBe('tool')
    expect(markers.filter((m) => m.turnIndex === 3).every((m) => m.failedTool === false)).toBe(true)
  })

  it('reads an agent_error as a failed tool too', () => {
    const turns = hydrateTurns([
      { role: 'user', content: 'go' },
      {
        role: 'tool',
        content: 'Read',
        meta: {
          tool_call_id: 't1',
          done: true,
          agent_error: { code: 'ENOENT', what: 'missing file', why: 'no such path', fix: 'check the path' },
        },
      },
      { role: 'assistant', content: 'could not read it' },
    ])
    expect(deriveSessionMarkers(turns).filter((m) => m.failedTool)).toHaveLength(2)
  })

  it('gives a successful tool in a failed turn the turn’s failed state', () => {
    const turns = hydrateTurns([
      { role: 'user', content: 'go' },
      { role: 'tool', content: 'Read', meta: { tool_call_id: 't1', done: true } },
      { role: 'tool', content: 'Terminal', meta: { tool_call_id: 't2', done: true, ok: false } },
      { role: 'assistant', content: 'partly done' },
    ])
    const markers = deriveSessionMarkers(turns).filter((m) => m.kind === 'tool')
    expect(markers.map((m) => m.label)).toEqual(['Read', 'Terminal'])
    expect(markers.every((m) => m.failedTool)).toBe(true)
  })
})

describe('deriveSessionMarkers — important events', () => {
  it('marks approvals, errors and subagent tools apart from plain tools', () => {
    const turns = hydrateTurns([
      { role: 'user', content: 'delegate this' },
      { role: 'permission', content: 'Terminal', meta: { approval_id: 'a1', tool: 'Terminal', risk: 'destructive' } },
      { role: 'tool', content: 'Task', meta: { tool_call_id: 't1', tool: 'Task', done: true } },
      { role: 'tool', content: 'Read', meta: { tool_call_id: 't2', tool: 'Read', done: true } },
      { role: 'error', content: 'the model returned an error' },
      { role: 'assistant', content: 'stopped' },
    ])
    const markers = deriveSessionMarkers(turns)
    expect(markers.map((m) => m.kind)).toEqual(['turn', 'turn', 'approval', 'subagent', 'tool', 'error'])
    expect(markers.map((m) => m.label)).toEqual([
      'delegate this', 'stopped', 'Terminal', 'Task', 'Read', 'the model returned an error',
    ])
    expect(markers.slice(1).every((m) => m.jumpIndex === 1)).toBe(true)
  })

  it('keeps process activity and drops the per-turn ledger lines', () => {
    const turns: ChatTurn[] = [
      userTurn('q'),
      {
        role: 'assistant',
        segments: [
          { kind: 'thinking', text: 'hmm' },
          { kind: 'activity', text: 'hook: pre-commit ran', activityKind: 'hook' },
          { kind: 'activity', text: 'fed 3 memories', activityKind: 'context' },
          { kind: 'activity', text: 'learned a lesson', activityKind: 'learned' },
          { kind: 'activity', text: 'Turn complete: 4 events', activityKind: 'stats' },
          { kind: 'activity', text: '', activityKind: 'headroom' },
          { kind: 'text', text: 'done' },
        ],
      },
    ]
    const markers = deriveSessionMarkers(turns)
    expect(markers.map((m) => m.kind)).toEqual(['turn', 'turn', 'activity'])
    expect(markers[2].label).toBe('hook: pre-commit ran')
  })

  it('names an unnamed tool rather than emitting a blank marker', () => {
    const turns: ChatTurn[] = [
      { role: 'assistant', segments: [{ kind: 'tool', id: 'x', tool: '', done: true }] },
    ]
    expect(labels(turns)).toEqual(['Assistant', 'tool'])
  })
})

describe('deriveSessionMarkers — ordering', () => {
  it('walks turns in order and events in segment order inside each turn', () => {
    const turns = hydrateTurns([
      { role: 'user', content: 'first' },
      { role: 'tool', content: 'Read', meta: { tool_call_id: 't1', done: true } },
      { role: 'permission', content: 'Terminal', meta: { approval_id: 'a1', tool: 'Terminal' } },
      { role: 'assistant', content: 'answer one' },
      { role: 'user', content: 'second' },
      { role: 'error', content: 'boom' },
      { role: 'assistant', content: 'answer two' },
    ])
    expect(kinds(turns)).toEqual(['turn', 'turn', 'tool', 'approval', 'turn', 'turn', 'error'])
    const markers = deriveSessionMarkers(turns)
    expect(markers.map((m) => m.turnIndex)).toEqual([0, 1, 1, 1, 2, 3, 3])
    expect(markers.map((m) => m.id)).toEqual(['t0', 't1', 't1s0', 't1s1', 't2', 't3', 't3s0'])
    expect(new Set(markers.map((m) => m.id)).size).toBe(markers.length)
  })

  it('is a pure function — same input, same output, no mutation', () => {
    const turns = hydrateTurns([
      { role: 'user', content: 'q' },
      { role: 'tool', content: 'Read', meta: { tool_call_id: 't1', done: true } },
      { role: 'assistant', content: 'a' },
    ])
    const before = JSON.stringify(turns)
    expect(deriveSessionMarkers(turns)).toEqual(deriveSessionMarkers(turns))
    expect(JSON.stringify(turns)).toBe(before)
  })
})

describe('deriveSessionMarkers — agrees with the durable backend index', () => {
  const fixture = JSON.parse(
    readFileSync(join(process.cwd(), '../../checks/runtime/fixtures/chat_index_parity.json'), 'utf8'),
  ) as { messages: HistMsg[]; expected: Record<string, unknown>[] }

  it('derives the shared vector marker for marker, coordinates included', () => {
    const markers = deriveSessionMarkers(hydrateTurns(fixture.messages))
    expect(markers.map((m) => ({
      id: m.id, kind: m.kind, label: m.label, role: m.role,
      turn_index: m.turnIndex, jump_index: m.jumpIndex, failed_tool: m.failedTool,
    }))).toEqual(fixture.expected)
  })

  it('every jump coordinate is the one forking that turn would take', () => {
    const turns = hydrateTurns(fixture.messages)
    for (const m of deriveSessionMarkers(turns)) expect(m.jumpIndex).toBe(branchIndexOf(turns, m.turnIndex))
  })
})
