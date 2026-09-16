export type DraftAction = { type: 'edit'; text: string } | { type: 'sync'; value: string }
export interface DraftState { text: string; source: string }
export const createDraft = (value: string): DraftState => ({ text: value, source: value })
export function draftReducer(state: DraftState, action: DraftAction): DraftState {
  if (action.type === 'edit') return { ...state, text: action.text }
  return action.value === state.source ? state : createDraft(action.value)
}

export function commitNumber(text: string, value: number, min?: number, max?: number) {
  const parsed = Number(text)
  const valid = text !== '' && !Number.isNaN(parsed)
  const next = valid ? Math.min(max ?? Infinity, Math.max(min ?? -Infinity, parsed)) : value
  return { text: String(next), changed: valid && next !== value, value: next }
}

export function addChip(values: string[], draft: string, max?: number): string[] | null {
  const candidate = draft.trim().replace(/,$/, '')
  return candidate && !values.includes(candidate) && (!max || values.length < max) ? [...values, candidate] : null
}

export function fieldNaming(published?: string, explicit?: string, name?: string, preferPublished = false) {
  const claim = !!published && (preferPublished || (!explicit && !name))
  return { 'aria-labelledby': claim ? published : undefined, 'aria-label': claim ? undefined : explicit }
}

export const isHexColor = (value: string) => /^#[\da-f]{6}$/i.test(value)
export function scalarText(value: number, unit = '') {
  if (unit === 'px' || unit === '%') return `${Math.round(value)}${unit}`
  return unit ? `${value.toFixed(1)}${unit}` : `${value.toFixed(2)}×`
}
export function rangeProgress(value: number, min: number, max: number) {
  return max > min ? Math.max(0, Math.min(100, (value - min) / (max - min) * 100)) : 0
}
