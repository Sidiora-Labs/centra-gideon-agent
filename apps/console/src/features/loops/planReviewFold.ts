import type { PlanNames } from './planNaming'

export interface PlanReviewState {
  buffer: string
  complete: boolean
  names: PlanNames | null
  confirmation: string | null
  demotedReason: string | null
}
export const emptyPlanReview = (): PlanReviewState => ({ buffer: '', complete: false, names: null, confirmation: null, demotedReason: null })
type Payload = Record<string, unknown>
type Fold = (state: PlanReviewState, payload: Payload) => Partial<PlanReviewState>
const isObject = (value: unknown): value is Payload => Boolean(value) && typeof value === 'object'

function namePatch(prior: PlanNames | null, incoming: PlanNames): PlanNames {
  const merged = { ...prior, ...incoming }
  for (const key of ['title', 'description'] as const) merged[key] = incoming[key] ?? prior?.[key]
  merged.labels = Object.assign({}, prior?.labels, incoming.labels)
  return merged
}

const eventFolds: Record<string, Fold> = {
  plan_streaming: (state, payload) => ({
    buffer: typeof payload.buffer === 'string' ? payload.buffer : state.buffer + (typeof payload.chunk === 'string' ? payload.chunk : ''),
    complete: state.complete || payload.done === true,
    names: isObject(payload.names) ? namePatch(state.names, payload.names) : state.names,
  }),
  revision: (state, payload) => {
    const source = payload.labels ?? (isObject(payload.names) ? payload.names.labels : undefined)
    const labels = isObject(source) ? Object.fromEntries(Object.entries(source).filter(([, value]) => typeof value === 'string')) as Record<string, string> : {}
    const replacement = typeof payload.buffer === 'string'
    return {
      names: isObject(payload.names) || payload.labels ? namePatch(state.names, { labels }) : state.names,
      buffer: replacement ? payload.buffer as string : state.buffer,
      complete: replacement ? payload.done === true : state.complete,
    }
  },
  confirmation: (_, payload) => ({ confirmation: [payload.done, payload.resolved].includes(true) ? null : String(payload.prompt ?? 'Confirm the plan before it runs.') }),
  demotion: (_, payload) => ({ demotedReason: String(payload.reason ?? 'Confidence dropped — switched to per-stage approval.') }),
}

export function planReviewReducer(state: PlanReviewState, event: string, data?: unknown): PlanReviewState {
  const fold = Object.prototype.hasOwnProperty.call(eventFolds, event) ? eventFolds[event] : undefined
  return fold ? { ...state, ...fold(state, (data ?? {}) as Payload) } : state
}
