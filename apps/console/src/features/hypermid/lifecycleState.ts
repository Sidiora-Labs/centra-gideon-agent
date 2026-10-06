import type {
  HypermidCursorWire,
  HypermidLifecycleAction,
  HypermidOperationPlanWire,
  HypermidOperationReceiptWire,
} from '../../shared/data/api'

export type DataDisposition = 'retain' | 'export' | 'purge'
export type LifecycleRecoveryState = 'committed' | 'resumable' | 'rollback_available' | 'failed' | 'cancelled' | 'outcome_unknown'

export interface LifecyclePlan extends HypermidOperationPlanWire {
  current_version?: string | null
  target_version?: string | null
  data_disposition?: DataDisposition | null
  source_digest?: string | null
  destination?: string | null
  rollback_digest?: string | null
  staging_id?: string | null
  resume_after?: HypermidCursorWire | null
  estimated_items?: number | null
  estimated_bytes?: number | null
  inventory?: Record<string, string[]>
  exclusions?: string[]
  binary_path?: string | null
  binary_digest?: string | null
  operator_identity?: { username: string; uid?: number | null } | null
  params?: {
    current_enrollment?: { digest: string; operations: string[]; resources: string[]; expires_ms: number } | null
    permission_additions?: { operations: string[]; resources: string[] } | null
    local_enrollment?: {
      operations: string[]
      resources: string[]
      expires_ms: number
    } | null
  } | null
  checks?: { packaged_binary_verified: boolean } | null
}

export interface LifecycleInstallEvidence {
  binaryPath: string
  binaryDigest: string
  operator: string
  operations: string[]
  resources: string[]
  expiresMs: number | null
  packagedBinaryVerified: boolean | null
  currentEnrollmentDigest: string
  addedOperations: string[]
  addedResources: string[]
}

export function lifecycleInstallEvidence(plan: LifecyclePlan): LifecycleInstallEvidence {
  const enrollment = plan.params?.local_enrollment
  const identity = plan.operator_identity
  return {
    binaryPath: plan.binary_path || '',
    binaryDigest: plan.binary_digest || '',
    operator: identity ? `${identity.username}${identity.uid == null ? '' : ` (UID ${identity.uid})`}` : '',
    operations: enrollment ? [...enrollment.operations] : [],
    resources: enrollment ? [...enrollment.resources] : [],
    expiresMs: enrollment?.expires_ms ?? null,
    packagedBinaryVerified: plan.checks?.packaged_binary_verified ?? null,
    currentEnrollmentDigest: plan.params?.current_enrollment?.digest || '',
    addedOperations: plan.params?.permission_additions?.operations || [],
    addedResources: plan.params?.permission_additions?.resources || [],
  }
}

export type LifecycleReceipt = HypermidOperationReceiptWire

export interface LifecycleDraft {
  action: HypermidLifecycleAction
  target_version: string
  data_disposition: DataDisposition
  source: string
  destination: string
}

export const LIFECYCLE_ACTIONS: ReadonlyArray<{ id: HypermidLifecycleAction; label: string; detail: string }> = [
  { id: 'install', label: 'Install', detail: 'Install a verified Hypermid release for this platform.' },
  { id: 'update', label: 'Update', detail: 'Stage a verified release and preserve rollback material.' },
  { id: 'uninstall', label: 'Uninstall', detail: 'Remove runtime wiring with an explicit user-data disposition.' },
  { id: 'migrate', label: 'Migrate', detail: 'Stage and validate existing Gideon knowledge before visibility.' },
  { id: 'export', label: 'Export', detail: 'Create a verified portable artifact without credentials or derived state.' },
  { id: 'restore', label: 'Restore', detail: 'Verify and stage a portable artifact before replacing visible state.' },
  { id: 'rollback', label: 'Rollback', detail: 'Return to previously verified rollback material.' },
]

export function lifecyclePlanBody(draft: LifecycleDraft): Record<string, unknown> {
  const body: Record<string, unknown> = { params: {} }
  if (draft.action === 'install' || draft.action === 'update') body.target_version = draft.target_version.trim()
  if (draft.action === 'uninstall') body.data_disposition = draft.data_disposition
  if (draft.action === 'migrate' || draft.action === 'restore') body.source = draft.source.trim()
  if (draft.action === 'export') body.destination = draft.destination.trim()
  return body
}

export function validateLifecycleDraft(draft: LifecycleDraft): string {
  if ((draft.action === 'install' || draft.action === 'update') && !draft.target_version.trim()) return 'Enter the release version to review.'
  if ((draft.action === 'migrate' || draft.action === 'restore') && !draft.source.trim()) return 'Enter the source artifact or store to verify.'
  if (draft.action === 'export' && !draft.destination.trim()) return 'Enter the export destination to review.'
  return ''
}

export function decideLifecycleApply(
  plan: LifecyclePlan,
  reviewedDigest: string,
  destructiveConfirmed: boolean,
  purgeConfirmed: boolean,
  now = Date.now(),
): { allowed: boolean; reason: string } {
  if (reviewedDigest !== plan.plan_digest) return { allowed: false, reason: 'Review the current plan and digest before applying it.' }
  if (!Number.isFinite(Date.parse(plan.expires_at)) || Date.parse(plan.expires_at) <= now) return { allowed: false, reason: 'This plan expired. Prepare and review a new plan.' }
  if (plan.blockers.length > 0) return { allowed: false, reason: 'Resolve every reported blocker and prepare a new plan.' }
  if (plan.destructive && !destructiveConfirmed) return { allowed: false, reason: 'Confirm the listed destructive effects.' }
  if (plan.data_disposition === 'purge' && !purgeConfirmed) return { allowed: false, reason: 'Confirm permanent user-data purge separately.' }
  if (plan.data_disposition !== 'purge' && purgeConfirmed) return { allowed: false, reason: 'Purge confirmation does not match this reviewed plan.' }
  return { allowed: true, reason: '' }
}

export function recoveryAction(state: LifecycleRecoveryState): 'none' | 'resume' | 'rollback' | 'recheck' {
  if (state === 'resumable') return 'resume'
  if (state === 'rollback_available') return 'rollback'
  if (state === 'outcome_unknown') return 'recheck'
  return 'none'
}

const LAST_LIFECYCLE_JOB_KEY = 'gideon:hypermid:last-lifecycle-job'

export function rememberLifecycleJob(jobId: string): void {
  if (!jobId) return
  try { localStorage.setItem(LAST_LIFECYCLE_JOB_KEY, jobId) } catch {}
}

export function rememberedLifecycleJob(): string {
  try { return localStorage.getItem(LAST_LIFECYCLE_JOB_KEY) || '' } catch { return '' }
}
