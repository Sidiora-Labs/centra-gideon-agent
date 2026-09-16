import type { OnboardingState } from '../../shared/data/api'
export type StepId = 'name' | 'import' | 'essentials' | 'try' | 'ready'
export const ORDER: StepId[] = ['name', 'import', 'essentials', 'try', 'ready']
export const TITLES: Record<StepId, string> = { name: 'Your name', import: 'Bring your setup over', essentials: 'Essential apps', try: 'Try one', ready: 'All set' }
export interface SetupState {
  step: StepId; draft: string; name: string; readiness: OnboardingState | null
  resume: StepId | null; completedTrials: number; imported: string; model: string; tried: string; showEverything: boolean
}
export const initialSetup: SetupState = { step: 'name', draft: '', name: '', readiness: null, resume: null, completedTrials: 0, imported: '', model: '', tried: '', showEverything: false }
export type SetupAction =
  | { type: 'loaded'; value: OnboardingState }
  | { type: 'draft'; value: string }
  | { type: 'name' }
  | { type: 'visit'; step: StepId }
  | { type: 'import' | 'essentials' | 'try'; summary: string }
  | { type: 'disclosure'; value: boolean }
export function setupReducer(state: SetupState, action: SetupAction): SetupState {
  switch (action.type) {
    case 'loaded': return { ...state, readiness: action.value, resume: action.value.step === 'first_success' ? 'try' : action.value.step === 'essentials' ? 'essentials' : null,
      model: action.value.step === 'first_success' ? action.value.needs_model ? 'Set up later' : action.value.essentials?.model || 'Ready to chat' : state.model,
      completedTrials: Object.values(action.value.first_success ?? {}).filter(Boolean).length }
    case 'draft': return { ...state, draft: action.value }
    case 'name': return state.draft.trim() ? { ...state, name: state.draft.trim(), step: state.resume ?? 'import' } : state
    case 'visit': return { ...state, step: action.step }
    case 'import': return { ...state, imported: action.summary, step: 'essentials' }
    case 'essentials': return { ...state, model: action.summary, step: 'try' }
    case 'try': return { ...state, tried: action.summary === 'Skipped' && state.completedTrials > 0 ? `${state.completedTrials} of 3 tried` : action.summary, step: 'ready' }
    case 'disclosure': return { ...state, showEverything: action.value }
  }
}
