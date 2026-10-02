import type {
  HypermidOperationPlanWire,
  HypermidOperationReceiptWire,
  HypermidOperationStepWire,
} from '../../shared/data/api'

export type MaintenanceAction =
  | 'integrity_check'
  | 'reconcile'
  | 'reindex'
  | 'compact'
  | 'cleanup_stale_cache'
  | 'rebuild_derivatives'

export type OperationStep = HypermidOperationStepWire
export type MaintenancePlan = HypermidOperationPlanWire
export type MaintenanceReceipt = HypermidOperationReceiptWire

export const MAINTENANCE_ACTIONS: ReadonlyArray<{ id: MaintenanceAction; label: string; detail: string }> = [
  { id: 'integrity_check', label: 'Integrity check', detail: 'Verify authoritative and derived store integrity.' },
  { id: 'reconcile', label: 'Reconcile state', detail: 'Compare durable intents and repair recoverable incomplete work.' },
  { id: 'reindex', label: 'Reindex', detail: 'Rebuild search indexes from authoritative records.' },
  { id: 'compact', label: 'Compact storage', detail: 'Reclaim safe storage after reviewing the affected steps.' },
  { id: 'cleanup_stale_cache', label: 'Clean stale caches', detail: 'Remove derived cache entries already marked stale.' },
  { id: 'rebuild_derivatives', label: 'Rebuild derivatives', detail: 'Regenerate derived state from authoritative inputs.' },
]

export interface ApplyDecision {
  allowed: boolean
  reason: string
}

export function decideMaintenanceApply(
  plan: MaintenancePlan,
  reviewedDigest: string,
  destructiveConfirmed: boolean,
  now = Date.now(),
): ApplyDecision {
  if (reviewedDigest !== plan.plan_digest) return { allowed: false, reason: 'Review the current plan before applying it.' }
  if (!Number.isFinite(Date.parse(plan.expires_at)) || Date.parse(plan.expires_at) <= now) {
    return { allowed: false, reason: 'This plan expired. Prepare and review a new plan.' }
  }
  if (plan.blockers.length > 0) return { allowed: false, reason: 'Resolve every reported blocker and prepare a new plan.' }
  if (plan.destructive && !destructiveConfirmed) {
    return { allowed: false, reason: 'Confirm the destructive effects after reviewing the plan.' }
  }
  return { allowed: true, reason: '' }
}

export function receiptIsTerminal(receipt: MaintenanceReceipt): boolean {
  return receipt.state !== 'running'
}

export function receiptProgress(receipt: MaintenanceReceipt): { complete: number; total: number } {
  return {
    complete: receipt.steps.filter((step) => ['committed', 'skipped', 'failed', 'cancelled'].includes(step.state)).length,
    total: receipt.steps.length,
  }
}

const LAST_JOB_KEY = 'gideon:hypermid:last-maintenance-job'

export function rememberMaintenanceJob(jobId: string): void {
  if (!jobId) return
  try { localStorage.setItem(LAST_JOB_KEY, jobId) } catch {}
}

export function rememberedMaintenanceJob(): string {
  try { return localStorage.getItem(LAST_JOB_KEY) || '' } catch { return '' }
}

export function forgetMaintenanceJob(jobId?: string): void {
  try {
    if (!jobId || localStorage.getItem(LAST_JOB_KEY) === jobId) localStorage.removeItem(LAST_JOB_KEY)
  } catch {}
}
