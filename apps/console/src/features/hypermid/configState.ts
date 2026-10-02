export type HypermidMode = 'off' | 'pass_through' | 'shadow' | 'primary'
export type HypermidOverflowPolicy = 'reclaim_then_refuse' | 'refuse_immediately'
export type HypermidRefusalPolicy = 'refuse' | 'compatible_last_known_good' | 'host_passthrough'

export interface HypermidRuntimeConfigValue {
  mode: HypermidMode
  overflow_policy: HypermidOverflowPolicy
  refusal_policy: HypermidRefusalPolicy
  features: {
    background_summaries: boolean
    reduction_tools: boolean
    automatic_reclaim: boolean
    nudges: boolean
    subagent_contributions: boolean
    synthetic_hook_blocks: boolean
  }
}

export interface RevisionedDraft<T> {
  base: T
  draft: T
  revision: string
  policy_revision?: number
  conflict?: { current: T; revision: string; policy_revision?: number; message: string }
}

export function openRevisionedDraft<T>(value: T, revision: string, policyRevision?: number): RevisionedDraft<T> {
  return { base: value, draft: value, revision, policy_revision: policyRevision }
}

export function rejectRevisionedDraft<T>(
  state: RevisionedDraft<T>,
  current: T,
  revision: string,
  message: string,
  policyRevision?: number,
): RevisionedDraft<T> {
  return { ...state, conflict: { current, revision, policy_revision: policyRevision, message } }
}

export function reloadRevisionedDraft<T>(state: RevisionedDraft<T>): RevisionedDraft<T> {
  if (!state.conflict) return state
  return openRevisionedDraft(state.conflict.current, state.conflict.revision, state.conflict.policy_revision)
}

export function reapplyRevisionedDraft<T>(state: RevisionedDraft<T>): RevisionedDraft<T> {
  if (!state.conflict) return state
  return {
    base: state.conflict.current,
    draft: state.draft,
    revision: state.conflict.revision,
    policy_revision: state.conflict.policy_revision,
  }
}

export function humanUnknown<T extends string | number | boolean>(value: T | null | undefined, format: (value: T) => string = String): string {
  return value == null ? 'Unknown' : format(value)
}

export function modelIsReady(model: { availability: string; health: string; last_probe_at?: string | null }): boolean {
  return model.availability === 'available' && model.health === 'healthy' && Boolean(model.last_probe_at)
}

const FORBIDDEN_CREDENTIAL_FIELDS = new Set(['value', 'secret', 'token', 'password', 'credential'])

export function containsCredentialValue(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsCredentialValue)
  if (!value || typeof value !== 'object') return false
  return Object.entries(value).some(([key, child]) => FORBIDDEN_CREDENTIAL_FIELDS.has(key.toLowerCase()) || containsCredentialValue(child))
}
