import { describe, expect, it } from 'vitest'
import { hydrateTurns } from './chatTypes'
import type { HistMsg } from './chatTypes'

describe('persisted ACP tool kinds', () => {
  it.each(['read', 'edit', 'execute'])('hydrates a declared %s kind', (kind) => {
    const turns = hydrateTurns([{ role: 'tool', content: 'Inspect', meta: { kind, tool_call_id: 'call-1' } }])
    expect(turns[0].segments[0]).toMatchObject({ kind: 'tool', toolKind: kind, done: true })
  })

  it.each([undefined, ''])('does not invent a kind for legacy or empty metadata', (kind) => {
    const turns = hydrateTurns([{ role: 'tool', content: 'Read file', meta: { kind } }])
    expect(turns[0].segments[0]).toMatchObject({ kind: 'tool', toolKind: undefined })
  })

  it('hydrates a later declared kind and retains it through empty result rows', () => {
    const rows: HistMsg[] = [
      { role: 'tool', content: 'Inspect', meta: { tool_call_id: 'call-1' } },
      { role: 'tool', content: 'Inspect', meta: { tool_call_id: 'call-1', kind: 'read' } },
      { role: 'tool', content: 'Inspect', meta: { tool_call_id: 'call-1', kind: '', output: 'contents', done: true } },
    ]
    const turns = hydrateTurns(rows, true)
    expect(turns[0].segments).toHaveLength(1)
    expect(turns[0].segments[0]).toMatchObject({ toolKind: 'read', output: 'contents', done: true })
  })

  it('keeps permission tool_kind distinct from tool-row kind', () => {
    const turns = hydrateTurns([
      { role: 'permission', content: 'Approve', meta: { tool_kind: 'execute' } },
      { role: 'tool', content: 'Inspect', meta: { kind: 'read' } },
    ])
    expect(turns[0].segments[0]).toMatchObject({ kind: 'approval', toolKind: 'execute' })
    expect(turns[0].segments[1]).toMatchObject({ kind: 'tool', toolKind: 'read' })
  })
})
