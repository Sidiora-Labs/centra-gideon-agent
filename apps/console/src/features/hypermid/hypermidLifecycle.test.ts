import { beforeEach, describe, expect, it } from 'vitest'
import {
  decideLifecycleApply,
  lifecycleInstallEvidence,
  lifecyclePlanBody,
  recoveryAction,
  rememberedLifecycleJob,
  rememberLifecycleJob,
  validateLifecycleDraft,
  type LifecycleDraft,
  type LifecyclePlan,
} from './lifecycleState'

const draft: LifecycleDraft = {
  action: 'uninstall',
  target_version: '',
  data_disposition: 'purge',
  source: '',
  destination: '',
}
const plan: LifecyclePlan = {
  plan_id: 'plan-uninstall',
  operation: 'lifecycle.uninstall.apply',
  scope: { owner_id: 'owner-1', project_id: 'project-1' },
  created_at: '2026-10-02T12:00:00Z',
  expires_at: '2026-10-02T13:00:00Z',
  plan_digest: 'a'.repeat(64),
  destructive: true,
  restart_required: false,
  data_disposition: 'purge',
  inventory: { runtime: ['service'], user_data: ['memory.sqlite3'] },
  blockers: [],
  steps: [{ id: 'remove-runtime', title: 'Remove runtime', effect: 'delete_authoritative', state: 'planned' }],
}

describe('Hypermid lifecycle review and recovery state', () => {
  beforeEach(() => localStorage.clear())

  it('keeps purge bound to its reviewed plan and exposes only authoritative recovery actions', () => {
    expect(validateLifecycleDraft(draft)).toBe('')
    expect(lifecyclePlanBody(draft)).toEqual({ params: {}, data_disposition: 'purge' })
    expect(decideLifecycleApply(plan, plan.plan_digest, true, false, Date.parse('2026-10-02T12:30:00Z')).reason).toMatch(/purge/i)
    expect(decideLifecycleApply(plan, plan.plan_digest, true, true, Date.parse('2026-10-02T12:30:00Z'))).toEqual({ allowed: true, reason: '' })
    expect(decideLifecycleApply({ ...plan, data_disposition: 'retain' }, plan.plan_digest, true, true, Date.parse('2026-10-02T12:30:00Z')).allowed).toBe(false)
    expect(decideLifecycleApply(plan, plan.plan_digest, true, true, Date.parse(plan.expires_at)).reason).toMatch(/expired/i)

    expect(recoveryAction('resumable')).toBe('resume')
    expect(recoveryAction('rollback_available')).toBe('rollback')
    expect(recoveryAction('outcome_unknown')).toBe('recheck')
    expect(recoveryAction('committed')).toBe('none')

    rememberLifecycleJob('job-lifecycle-4')
    expect(rememberedLifecycleJob()).toBe('job-lifecycle-4')
  })

  it('selects only non-secret install evidence for exact operator review', () => {
    const evidence = lifecycleInstallEvidence({
      ...plan,
      operation: 'lifecycle.install.apply',
      binary_path: '/opt/gideon/hypermid-daemon',
      binary_digest: 'b'.repeat(64),
      operator_identity: { username: 'gideon', uid: 10001 },
      params: {
        local_enrollment: {
          operations: ['read', 'append'],
          resources: ['memory-records', 'memory-maintenance'],
          expires_ms: 1_790_960_000_000,
        },
        credential_id: 'must-not-render',
        capability_id: 'must-not-render',
      } as LifecyclePlan['params'],
      checks: { packaged_binary_verified: true },
    })
    expect(evidence).toEqual({
      binaryPath: '/opt/gideon/hypermid-daemon',
      binaryDigest: 'b'.repeat(64),
      operator: 'gideon (UID 10001)',
      operations: ['read', 'append'],
      resources: ['memory-records', 'memory-maintenance'],
      expiresMs: 1_790_960_000_000,
      packagedBinaryVerified: true,
    })
    expect(JSON.stringify(evidence)).not.toContain('must-not-render')
  })
})
