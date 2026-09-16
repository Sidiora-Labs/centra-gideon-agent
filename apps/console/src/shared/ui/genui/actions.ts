import { createContext, useContext } from 'react'
import { api } from '../../data/api'
import { composeWidgetActionText, publishWidgetAction } from '../widget/actionTurn'

export interface DualPayload { llmFriendlyMessage: string; humanFriendlyMessage: string }
export type GenUiProducer =
  | { kind: 'chat' }
  | { kind: 'workflow-gate'; runId: string; token: string }
  | { kind: 'tile'; viewId: string; ref: string }
export interface GenUiActionResult {
  ok: boolean
  outcome: 'chat-turn' | 'gate-resolved' | 'tile-refired' | 'refused' | 'error'
  message?: string
}
export type GenUiEmit = (input: { action: string; label?: string; payload?: Record<string, unknown> }) => void | Promise<void>
export interface GenUiHost { producer: GenUiProducer; onResolved?: () => void }

type RawAction = { action: string; payload?: Record<string, unknown> }
export type GenUiActionPlan =
  | { kind: 'chat'; text: string; label: string }
  | { kind: 'workflow-gate'; runId: string; request: { answer: string | Record<string, unknown>; resume_token: string } }
  | { kind: 'tile'; viewId: string; request: { ref: string; action: string; payload?: Record<string, unknown> } }

export function humanizeAction(action: string): string {
  const words = (action || '').replace(/[_-]+|([a-z])([A-Z])/g, (_match, lower: string | undefined, upper: string | undefined) => lower ? `${lower} ${upper}` : ' ').trim()
  return words ? words[0].toUpperCase() + words.slice(1) : 'Action'
}

export function composeDualPayload(input: {
  action: string; label?: string; payload?: Record<string, unknown>; live?: { saved: boolean; slug: string }
}): DualPayload | null {
  const text = composeWidgetActionText(input.action, input.payload, input.live)
  return text === null ? null : { llmFriendlyMessage: text, humanFriendlyMessage: input.label?.trim() || humanizeAction(input.action) }
}

export function planGenUiAction(dual: DualPayload, producer: GenUiProducer, raw: RawAction): GenUiActionPlan {
  switch (producer.kind) {
    case 'workflow-gate': return {
      kind: producer.kind, runId: producer.runId,
      request: { resume_token: producer.token, answer: raw.payload && Object.keys(raw.payload).length ? raw.payload : dual.humanFriendlyMessage },
    }
    case 'tile': return { kind: producer.kind, viewId: producer.viewId, request: { ref: producer.ref, action: raw.action, payload: raw.payload } }
    default: return { kind: 'chat', text: dual.llmFriendlyMessage, label: dual.humanFriendlyMessage }
  }
}

const routingMessages = {
  'workflow-gate': { success: 'gate-resolved', refused: 'This gate could not be answered — it may already be resolved.', failure: 'Could not answer the gate.' },
  tile: { success: 'tile-refired', refused: 'That action is outside this tile’s frozen capability set.', failure: 'Could not re-fire this tile.' },
  chat: { success: 'chat-turn', refused: '', failure: 'Could not send the widget action.' },
} as const

export async function routeGenUiAction(dual: DualPayload, producer: GenUiProducer, raw: RawAction): Promise<GenUiActionResult> {
  const plan = planGenUiAction(dual, producer, raw)
  const messages = routingMessages[plan.kind]
  try {
    if (plan.kind === 'chat') {
      publishWidgetAction(plan.text, { label: plan.label })
      return { ok: true, outcome: 'chat-turn' }
    }
    const response = plan.kind === 'workflow-gate'
      ? await api.resumeWorkflowRun(plan.runId, plan.request)
      : await api.tileWidgetAction(plan.viewId, plan.request)
    if (response?.ok === false) {
      const message = plan.kind === 'tile' && 'message' in response && typeof response.message === 'string' ? response.message : ''
      return { ok: false, outcome: 'refused', message: message || messages.refused }
    }
    return { ok: true, outcome: messages.success }
  } catch (error) {
    return { ok: false, outcome: 'error', message: (error as Error)?.message || messages.failure }
  }
}

export const GenUiActionCtx = createContext<GenUiEmit>(() => {})
export const GenUiHostCtx = createContext<GenUiHost>({ producer: { kind: 'chat' } })
export const useGenUiAction = (): GenUiEmit => useContext(GenUiActionCtx)
export const useGenUiHost = (): GenUiHost => useContext(GenUiHostCtx)
