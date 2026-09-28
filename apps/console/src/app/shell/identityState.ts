export interface IdentityState { name: string; status: 'loading' | 'loaded' | 'failed'; error: string; revision: number; request: number }
export const initialIdentity: IdentityState = { name: '', status: 'loading', error: '', revision: 0, request: 0 }
export type IdentityAction =
  | { type: 'edit'; name: string }
  | { type: 'loadStarted'; request: number }
  | { type: 'loaded'; name: string; revision: number; request?: number }
  | { type: 'loadFailed'; error: string; request: number }
export function identityReducer(state: IdentityState, action: IdentityAction): IdentityState {
  if (action.type === 'edit') return { ...state, name: action.name.trim(), revision: state.revision + 1 }
  if (action.type === 'loadStarted') return { ...state, status: 'loading', error: '', request: action.request }
  if (action.type === 'loadFailed') {
    if (action.request !== state.request) return state
    return { ...state, status: 'failed', error: action.error }
  }
  if (action.request !== undefined && action.request !== state.request) return state
  return { ...state, status: 'loaded', error: '', name: action.revision === state.revision ? action.name : state.name }
}
