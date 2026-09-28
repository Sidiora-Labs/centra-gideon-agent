import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { hydrateTurns, type HistMsg } from './chatTypes'
import { ContextLedger } from './ContextLedger'

const line = 'Turn complete: 3 events, 1 tool calls, context 42% · $0.0123 · 1,200 in / 340 out tokens'
const restored: HistMsg[] = [
  { role: 'user', content: 'What changed?', ts: '2026-09-26T10:00:00Z' },
  { role: 'assistant', content: 'Two files changed.', ts: '2026-09-26T10:00:04Z', meta: { turn_telemetry: { line } } },
]

afterEach(cleanup)

describe('persisted turn telemetry hydration', () => {
  it('hydrates the stored display line into the existing telemetry row', () => {
    const assistant = hydrateTurns(restored).find((turn) => turn.role === 'assistant')
    const stats = assistant?.segments.find((segment) => segment.kind === 'activity' && segment.activityKind === 'stats')
    expect(stats).toEqual({ kind: 'activity', text: line, activityKind: 'stats' })
    render(<ContextLedger stats={stats?.kind === 'activity' ? stats.text : undefined} />)
    fireEvent.click(screen.getByRole('button', { name: /telemetry/i }))
    expect(screen.getByText(line)).toBeTruthy()
  })

  it('leaves older transcript rows without telemetry unchanged', () => {
    const older: HistMsg[] = [restored[0], { ...restored[1], meta: {} }]
    const assistant = hydrateTurns(older).find((turn) => turn.role === 'assistant')
    expect(assistant?.segments.some((segment) => segment.kind === 'activity' && segment.activityKind === 'stats')).toBe(false)
  })
})
