export type SliderQuestionKind = 'text' | 'choice' | 'slider' | 'boundary'

export const OTHER_CHOICE = 'Other (describe)'

export interface SliderQuestion {
  id: string
  prompt: string
  kind: SliderQuestionKind
  choices?: string[]
  recommended?: string
  required?: boolean
  min?: number
  max?: number
  step?: number
  phase?: string
  phaseIndex?: number
  phaseCount?: number
}

export interface SliderState {
  index: number
  answers: Record<string, string>
  custom: Record<string, boolean>
}

export type SliderAction =
  | { type: 'answer'; id: string; value: string }
  | { type: 'toggleCustom'; id: string; on: boolean }
  | { type: 'next'; total: number }
  | { type: 'back' }
  | { type: 'goto'; index: number; total: number }


export function initSliderState(questions: SliderQuestion[], seed: Record<string, string> = {}): SliderState {
  return questions.reduce<SliderState>((state, question) => {
    const saved = seed[question.id]
    const answer = saved != null && saved !== '' ? saved : question.recommended ?? ''
    if (!answer) return state
    state.answers[question.id] = answer
    if (question.kind === 'choice' && !question.choices?.includes(answer)) state.custom[question.id] = true
    return state
  }, { index: 0, answers: {}, custom: {} })
}

export function sliderReducer(state: SliderState, action: SliderAction): SliderState {
  if (action.type === 'answer' || action.type === 'toggleCustom') {
    const patch: Partial<SliderState> = {}
    if (action.type === 'answer') patch.answers = { ...state.answers, [action.id]: action.value }
    else {
      patch.custom = { ...state.custom, [action.id]: action.on }
      if (!action.on) patch.answers = { ...state.answers, [action.id]: '' }
    }
    return { ...state, ...patch }
  }
  const index = action.type === 'back' ? Math.max(0, state.index - 1)
    : action.type === 'next' ? Math.min(state.index + 1, action.total - 1)
    : Math.max(0, Math.min(action.index, action.total - 1))
  return { ...state, index }
}

export function isAnswered(question: SliderQuestion, state: SliderState): boolean {
  return Boolean(state.answers[question.id]?.trim())
}
export function canAdvance(question: SliderQuestion, state: SliderState): boolean {
  return question.required ? isAnswered(question, state) : true
}
export function canSubmit(questions: SliderQuestion[], state: SliderState): boolean {
  return !questions.some((question) => !canAdvance(question, state))
}
export function answerRecord(questions: SliderQuestion[], state: SliderState): Record<string, string> {
  return Object.fromEntries(questions.flatMap(({ id }) => {
    const answer = state.answers[id]?.trim()
    return answer ? [[id, answer]] : []
  }))
}
