export interface IdentityState { name: string; loaded: boolean; revision: number }
export const initialIdentity: IdentityState = { name: '', loaded: false, revision: 0 }
export type IdentityAction = { type: 'edit'; name: string } | { type: 'loaded'; name: string; revision: number }
export function identityReducer(state: IdentityState, action: IdentityAction): IdentityState {
  if (action.type === 'edit') return { ...state, name: action.name.trim(), revision: state.revision + 1 }
  return { ...state, loaded: true, name: action.revision === state.revision ? action.name : state.name }
}
