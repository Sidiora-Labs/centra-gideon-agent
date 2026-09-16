export interface IncidentState { active: boolean; reason: string; busy: boolean; revision: number }
export const initialIncident: IncidentState = { active: false, reason: '', busy: false, revision: 0 }
export type IncidentAction = { type: 'snapshot'; revision: number; active: boolean; reason: string } | { type: 'resume'; revision: number } | { type: 'settled'; revision: number; ok: boolean }
export function incidentReducer(state: IncidentState, action: IncidentAction): IncidentState {
  if (action.revision < state.revision) return state
  if (action.type === 'snapshot') return state.busy ? state : { ...state, active: action.active, reason: action.reason, revision: action.revision }
  if (action.type === 'resume') return { ...state, busy: true, revision: action.revision }
  return { ...state, busy: false, active: action.ok ? false : state.active, reason: action.ok ? '' : state.reason, revision: action.revision }
}
