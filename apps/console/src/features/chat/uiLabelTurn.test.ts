import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, it, expect } from 'vitest'
import { hydrateTurns, type HistMsg } from './chatTypes'

const MACHINE = '[UI] log_expense: {"amount":"12.40","vendor":"Acme"}'
const LABEL = 'Log expense'

const chatSource = readFileSync(join(process.cwd(), "src/features/ChatPage.tsx"), 'utf8')

function turnsFor(meta?: HistMsg['meta']) {
  return hydrateTurns([{ role: 'user', content: MACHINE, ts: '2026-08-24T00:00:00Z', meta }])
}

function textOf(turn: { segments: { kind: string; text?: string }[] }): string {
  return turn.segments.filter((s) => s.kind === 'text').map((s) => s.text ?? '').join('')
}

describe('a reloaded widget-action turn', () => {
  it('renders the human label and NOT the machine payload', () => {
    const [turn] = turnsFor({ ui_label: LABEL })
    expect(textOf(turn)).toBe(LABEL)
    expect(textOf(turn)).not.toContain('amount')
    expect(textOf(turn)).not.toContain('{')
  })

  it('does not stash the payload in the optimized-prompt disclosure either', () => {
    const [turn] = turnsFor({ ui_label: LABEL })
    expect(turn.optimized).toBeUndefined()
  })

  it('VACUITY: the same message without the label DOES render the raw payload', () => {
    const [turn] = turnsFor(undefined)
    expect(textOf(turn)).toBe(MACHINE)
    expect(textOf(turn)).toContain('{')
  })

  it('still honors the optimized-prompt provenance when there is no widget label', () => {
    const [turn] = turnsFor({ original: 'log an expense please' })
    expect(textOf(turn)).toBe('log an expense please')
    expect(turn.optimized).toBe(MACHINE)
  })
})

describe('the live send path (source-asserted — ChatPage is not mountable here)', () => {
  it('forwards the bridge label as the turn label', () => {
    expect(chatSource).toContain("useWidgetActionBridge((text, meta) => { void send(text, { uiLabel: meta.label }) })")
  })

  it('drains a staged non-chat action WITH its label', () => {
    expect(chatSource).toContain('void send(pending.text, { uiLabel: pending.label })')
  })

  it('builds the local bubble from the label first', () => {
    expect(chatSource).toContain('userTurn(uiLabel ?? original ?? t,')
  })

  it('persists the label so the reload path above has something to read', () => {
    expect(chatSource).toContain('if (uiLabel) meta.ui_label = uiLabel')
  })

  it('sends the MACHINE text to the model, never the label', () => {
    expect(chatSource).toContain('await api.sendChat(llmText, sid, meta, undefined, opts?.inputOrigin)')
  })
})
