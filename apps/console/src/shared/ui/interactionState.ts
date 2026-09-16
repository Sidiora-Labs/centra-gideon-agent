export interface Choice { value: string; label: string; group?: string; description?: string }
export interface ChoiceGroup { label: string; rows: Array<{ choice: Choice; index: number }> }
export interface ChoiceState { open: boolean; query: string; cursor: number; restoreFocus: boolean }
export type ChoiceAction =
  | { type: 'open' }
  | { type: 'close'; restore?: boolean }
  | { type: 'query'; value: string }
  | { type: 'cursor'; index: number; count: number }

export const closedChoice: ChoiceState = { open: false, query: '', cursor: 0, restoreFocus: false }

export function choiceReducer(state: ChoiceState, action: ChoiceAction): ChoiceState {
  switch (action.type) {
    case 'open': return { ...closedChoice, open: true }
    case 'close': return { ...state, open: false, restoreFocus: !!action.restore }
    case 'query': return { ...state, query: action.value, cursor: 0 }
    case 'cursor': return { ...state, cursor: Math.max(0, Math.min(action.index, action.count - 1)) }
  }
}

export function choiceProjection(choices: Choice[], query: string) {
  const needle = query.trim().toLowerCase()
  const buckets = new Map<string, Choice[]>()
  for (const choice of choices) {
    const searchable = [choice.label, choice.group ?? '', choice.description ?? ''].join(' ').toLowerCase()
    if (needle && !searchable.includes(needle)) continue
    const name = choice.group ?? ''
    const bucket = buckets.get(name)
    if (bucket) bucket.push(choice)
    else buckets.set(name, [choice])
  }
  const ordered: Choice[] = []
  const groups: ChoiceGroup[] = []
  for (const [label, choicesInGroup] of buckets) {
    groups.push({ label, rows: choicesInGroup.map((choice) => ({ choice, index: ordered.push(choice) - 1 })) })
  }
  return { groups, ordered }
}

export interface ResultCount { count: number; noun: string; active: boolean; singular?: string; empty?: string }
export function resultMessage(result: ResultCount): string {
  if (!result.active) return ''
  if (result.count === 0) return result.empty ?? `No matching ${result.noun}`
  const noun = result.count === 1 ? result.singular ?? result.noun.replace(/s$/, '') : result.noun
  return [result.count, noun].join(' ')
}

export function searchControlName(label: string): string {
  return `search-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}`
}

export function recordingReducer(state: 'idle' | 'listening', action: 'arm' | 'cancel' | 'commit') {
  if (action === 'arm') return 'listening'
  return state === 'idle' ? state : 'idle'
}
