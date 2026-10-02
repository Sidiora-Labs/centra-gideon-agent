import type { HypermidSecurityPlanWire, HypermidSecurityReceiptWire } from '../../shared/data/api'

export type SecurityRecoveryAction = 'backup' | 'restore'

export interface SecurityRecoveryDraft {
  action: SecurityRecoveryAction
  destination: string
  export_id: string
  artifact_path: string
  source_digest: string
}

export const initialSecurityRecoveryDraft: SecurityRecoveryDraft = {
  action: 'backup',
  destination: '',
  export_id: '',
  artifact_path: '',
  source_digest: '',
}

export function validateSecurityRecoveryDraft(draft: SecurityRecoveryDraft): string {
  if (draft.action === 'backup') {
    if (!draft.destination.trim()) return 'Choose a destination for the encrypted backup artifact.'
    if (draft.export_id && !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(draft.export_id)) {
      return 'The optional export ID is invalid.'
    }
    return ''
  }
  if (!draft.artifact_path.trim()) return 'Choose the encrypted backup artifact to restore.'
  if (!/^[a-f0-9]{64}$/.test(draft.source_digest)) {
    return 'Enter the exact 64-character source digest recorded by the backup receipt.'
  }
  return ''
}

export function securityRecoveryPlanBody(draft: SecurityRecoveryDraft): Record<string, unknown> {
  if (draft.action === 'backup') {
    return {
      destination: draft.destination.trim(),
      ...(draft.export_id.trim() ? { export_id: draft.export_id.trim() } : {}),
    }
  }
  return {
    artifact_path: draft.artifact_path.trim(),
    source_digest: draft.source_digest,
  }
}

export function securityRecoveryApplyDecision(
  plan: HypermidSecurityPlanWire,
  reviewedDigest: string,
  destructiveConfirmed: boolean,
  now = Date.now(),
): { allowed: boolean; reason: string } {
  if (reviewedDigest !== plan.plan_digest) {
    return { allowed: false, reason: 'Review this exact security plan and digest before applying it.' }
  }
  if (!Number.isFinite(Date.parse(plan.expires_at)) || Date.parse(plan.expires_at) <= now) {
    return { allowed: false, reason: 'This security plan expired. Prepare and review a new plan.' }
  }
  if (plan.blockers.length) {
    return { allowed: false, reason: 'Resolve every reported blocker and prepare a new plan.' }
  }
  if (plan.destructive && !destructiveConfirmed) {
    return { allowed: false, reason: 'Confirm the destructive restore effects after reviewing the plan.' }
  }
  return { allowed: true, reason: '' }
}

const LAST_JOB_KEY = 'gideon:hypermid:last-security-recovery-job'

export function rememberSecurityRecoveryJob(jobId: string): void {
  if (!jobId) return
  try { localStorage.setItem(LAST_JOB_KEY, jobId) } catch {}
}

export function rememberedSecurityRecoveryJob(): string {
  try { return localStorage.getItem(LAST_JOB_KEY) || '' } catch { return '' }
}

export function receiptNeedsRecovery(receipt: HypermidSecurityReceiptWire): boolean {
  return receipt.state === 'outcome_unknown' || receipt.effect_state === 'unknown'
}
