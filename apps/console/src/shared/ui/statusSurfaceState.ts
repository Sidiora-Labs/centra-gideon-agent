import type { DegradedSurface } from '../data/api'

export function progressFraction(value: number, maximum = 1) {
  return Number.isNaN(value) ? 0 : Math.max(0, Math.min(1, value / maximum))
}

export function progressArc(value: number, size: number) {
  const center = Math.max(0, Number.isFinite(size) ? size : 28) / 2
  const radius = Math.max(0, center - 2.5)
  const circumference = 2 * Math.PI * radius
  return { center, radius, circumference, offset: circumference * (1 - progressFraction(value)) }
}

export function progressWave(width: number) {
  const span = Math.max(0, Number.isFinite(width) ? width : 120)
  const nodes = [0.25, 0.5, 0.75, 1].map(fraction => span * fraction)
  return `M0 4 Q ${span / 8} 0 ${nodes.shift()} 4 ${nodes.map(x => `T ${x} 4`).join(' ')}`
}

export interface DegradedReading {
  surfaces: DegradedSurface[] | null
  failed: boolean
  provider: boolean | null
}
export const initialDegradedReading: DegradedReading = { surfaces: null, failed: false, provider: null }
export type DegradedEvent = { type: 'surfaces'; surfaces: DegradedSurface[] } | { type: 'failure' } | { type: 'provider'; value: boolean | null }
export function degradedReading(state: DegradedReading, event: DegradedEvent): DegradedReading {
  switch (event.type) {
    case 'surfaces': return { ...state, surfaces: event.surfaces, failed: false }
    case 'failure': return { ...state, failed: true }
    case 'provider': return { ...state, provider: event.value }
  }
}
export function surfaceLabel(value: string) {
  const words = value.replace(/[_-]/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}
export function missingModelLabel(value: string) {
  return ({ chat: 'Chat', embedding: 'Embedding', stt: 'Speech-to-text' } as Record<string, string>)[value] ?? surfaceLabel(value)
}
export function degradedPresentation(state: DegradedReading) {
  const down = state.surfaces?.filter(surface => !surface.available) ?? []
  const unknown = state.surfaces === null && state.failed
  const setup = !unknown && state.provider === false && down.length > 0
  const summary = unknown ? 'Status unknown' : setup ? 'Set up a model'
    : down.length === 1 ? `${surfaceLabel(down[0].surface)} degraded` : `${down.length} degraded`
  const detail = unknown
    ? 'Status unknown — the degraded-surfaces check could not be read, so this may be hiding a surface running without a model'
    : setup ? 'No model provider is configured yet — click to see what unlocks once you bind one'
      : `${summary} — ${down.length} surface${down.length === 1 ? '' : 's'} running without a model, click for detail`
  return { down, unknown, setup, summary, detail, visible: unknown || down.length > 0 }
}

export type FeedbackVerdict = 'up' | 'down'
export interface FeedbackState { verdict: FeedbackVerdict | null; disabled: boolean; editing: boolean; reason: string; changed: boolean }
export const initialFeedback: FeedbackState = { verdict: null, disabled: false, editing: false, reason: '', changed: false }
export type FeedbackEvent = { type: 'reset' | 'failed' | 'toggle' | 'cancel' }
  | { type: 'hydrate'; verdict: FeedbackVerdict | null }
  | { type: 'reason'; value: string }
  | { type: 'record'; verdict: FeedbackVerdict }
export function feedbackState(state: FeedbackState, event: FeedbackEvent): FeedbackState {
  switch (event.type) {
    case 'reset': return initialFeedback
    case 'failed': return { ...state, disabled: true, editing: false }
    case 'hydrate': return state.changed ? state : { ...state, verdict: event.verdict }
    case 'toggle': return { ...state, editing: !state.editing }
    case 'reason': return { ...state, reason: event.value.slice(0, 500) }
    case 'cancel': return { ...state, editing: false, reason: '' }
    case 'record': return { ...state, verdict: event.verdict, editing: false, reason: '', changed: true }
  }
}
