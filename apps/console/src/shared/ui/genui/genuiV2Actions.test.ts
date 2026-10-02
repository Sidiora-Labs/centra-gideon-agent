import { afterEach, describe, expect, it } from 'vitest'
import { composeDualPayload, genUiActionReceipt, planGenUiAction, routeGenUiAction } from './actions'
import { WIDGET_ACTION_EVENT } from '../widget/actionTurn'

const listeners: EventListener[] = []
afterEach(() => { for (const listener of listeners.splice(0)) window.removeEventListener(WIDGET_ACTION_EVENT, listener) })

describe('GenUI action receipts and existing dispatchers', () => {
  it('publishes the actual chat event with human label and structured machine values, claiming only queued', async () => {
    const received: unknown[] = []
    const listener: EventListener = event => received.push((event as CustomEvent).detail)
    listeners.push(listener)
    window.addEventListener(WIDGET_ACTION_EVENT, listener)
    const input = { action: 'use_selected_option', label: 'Use this option', payload: { selection: 'option-b', notes: 'Keep the current region' } }
    const result = await routeGenUiAction(composeDualPayload(input)!, { kind: 'chat' }, input)
    expect(received).toEqual([{ text: '[UI] use_selected_option: {"selection":"option-b","notes":"Keep the current region"}', label: 'Use this option' }])
    expect(result).toEqual({ ok: true, outcome: 'chat-turn', message: 'Queued for this conversation.' })
  })

  it('requires explicit server acceptance for workflow and tile results', () => {
    for (const kind of ['workflow-gate', 'tile'] as const) {
      for (const value of [undefined, null, {}, { ok: false }, { ok: 'true' }, { resumed: true }]) expect(genUiActionReceipt(kind, value).ok).toBe(false)
      expect(genUiActionReceipt(kind, { ok: true }).ok).toBe(true)
      expect(genUiActionReceipt(kind, { ok: false, message: 'This operation is unavailable.' }).message).toBe('This operation is unavailable.')
    }
  })

  it('retains host-issued gate and tile authority while forwarding reviewed values', () => {
    const input = { action: 'choose', payload: { selection: 'option-b' } }
    const dual = composeDualPayload(input)!
    expect(planGenUiAction(dual, { kind: 'workflow-gate', runId: 'run-current', token: 'issued-gate-token' }, input)).toEqual({ kind: 'workflow-gate', runId: 'run-current', request: { resume_token: 'issued-gate-token', answer: input.payload } })
    expect(planGenUiAction(dual, { kind: 'tile', viewId: 'view-current', ref: 'artifact:choice' }, input)).toEqual({ kind: 'tile', viewId: 'view-current', request: { ref: 'artifact:choice', action: 'choose', payload: input.payload } })
  })
})
