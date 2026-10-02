import { beforeEach, describe, expect, it } from 'vitest'
import {
  decideMaintenanceApply,
  forgetMaintenanceJob,
  receiptIsTerminal,
  receiptProgress,
  rememberedMaintenanceJob,
  rememberMaintenanceJob,
  type MaintenancePlan,
  type MaintenanceReceipt,
} from './operationsState'

const digest = 'a'.repeat(64)
const plan: MaintenancePlan = {
  plan_id: 'plan-1',
  operation: 'maintenance.reindex',
  scope: { owner_id: 'owner-1', project_id: 'project-1' },
  created_at: '2026-10-02T11:00:00Z',
  expires_at: '2026-10-02T12:00:00Z',
  plan_digest: digest,
  destructive: false,
  restart_required: false,
  blockers: [],
  steps: [{ id: 'scan', title: 'Scan records', effect: 'read', state: 'planned' }],
}

describe('Hypermid operation review and receipt recovery', () => {
  beforeEach(() => localStorage.clear())

  it('applies only the exact current unblocked review and recovers authoritative job progress', () => {
    expect(decideMaintenanceApply(plan, digest, false, Date.parse('2026-10-02T11:30:00Z'))).toEqual({ allowed: true, reason: '' })
    expect(decideMaintenanceApply(plan, 'b'.repeat(64), false, Date.parse('2026-10-02T11:30:00Z')).allowed).toBe(false)
    expect(decideMaintenanceApply(plan, digest, false, Date.parse(plan.expires_at)).reason).toMatch(/expired/i)
    expect(decideMaintenanceApply({ ...plan, blockers: ['writer lease active'] }, digest, false, Date.parse('2026-10-02T11:30:00Z')).reason).toMatch(/blocker/i)
    expect(decideMaintenanceApply({ ...plan, destructive: true }, digest, false, Date.parse('2026-10-02T11:30:00Z')).reason).toMatch(/destructive/i)

    const receipt: MaintenanceReceipt = {
      job_id: 'job-7',
      operation: 'maintenance.reindex',
      scope: plan.scope,
      plan_digest: digest,
      state: 'running',
      started_at: '2026-10-02T11:31:00Z',
      finished_at: null,
      cursor: { epoch: 1, sequence: 9 },
      rollback_available: false,
      artifact_digest: null,
      error: null,
      steps: [
        { ...plan.steps[0], state: 'committed' },
        { id: 'write', title: 'Write index', effect: 'write_derivative', state: 'running' },
      ],
    }
    expect(receiptProgress(receipt)).toEqual({ complete: 1, total: 2 })
    expect(receiptIsTerminal(receipt)).toBe(false)
    expect(receiptIsTerminal({ ...receipt, state: 'outcome_unknown', finished_at: '2026-10-02T11:32:00Z' })).toBe(true)

    rememberMaintenanceJob(receipt.job_id)
    expect(rememberedMaintenanceJob()).toBe('job-7')
    forgetMaintenanceJob('another-job')
    expect(rememberedMaintenanceJob()).toBe('job-7')
    forgetMaintenanceJob(receipt.job_id)
    expect(rememberedMaintenanceJob()).toBe('')
  })
})
