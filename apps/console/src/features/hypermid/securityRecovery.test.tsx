import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { HypermidSecurityPlanWire, HypermidSecurityReceiptWire } from '../../shared/data/api'
import { SecurityRecovery } from './SecurityRecovery'
import {
  initialSecurityRecoveryDraft,
  receiptNeedsRecovery,
  securityRecoveryApplyDecision,
  securityRecoveryPlanBody,
  validateSecurityRecoveryDraft,
} from './securityRecoveryState'

const digest = 'a'.repeat(64)
const plan: HypermidSecurityPlanWire = {
  plan_id: 'security-restore-plan',
  operation: 'security.restore.apply',
  scope: { owner_id: 'owner', project_id: 'project' },
  created_at: '2026-10-02T12:00:00Z',
  expires_at: '2026-10-02T12:10:00Z',
  plan_digest: digest,
  authority_digest: 'b'.repeat(64),
  params_digest: 'c'.repeat(64),
  destructive: true,
  restart_required: true,
  blockers: [],
  steps: [{ id: 'verify-and-apply', title: 'Verify and apply restore', effect: 'write_authoritative', state: 'planned' }],
}

describe('Hypermid encrypted recovery snapshots', () => {
  it('renders a native recovery workflow without exposing secret or credential-handle inputs', () => {
    const html = renderToStaticMarkup(<SecurityRecovery />)
    expect(html).toContain('Encrypted backup and recovery')
    expect(html).toContain('encrypted, scope-bound recovery snapshots')
    expect(html).toContain('Credential authority')
    expect(html).toContain('Artifact destination')
    expect(html).toContain('Outcome recovery')
    expect(html).not.toContain('credential_handle')
    expect(html).not.toContain('credential_ref')
    expect(html).not.toContain('password')
    expect(html).not.toContain('portable JSONL')
  })

  it('requires exact source evidence, reviewed digest, and destructive confirmation', () => {
    const backup = securityRecoveryPlanBody({
      ...initialSecurityRecoveryDraft,
      destination: ' /protected/recovery.hmbk ',
      export_id: 'recovery-1',
    })
    expect(backup).toEqual({ destination: '/protected/recovery.hmbk', export_id: 'recovery-1' })
    expect(backup).not.toHaveProperty('credential_handle')
    expect(backup).not.toHaveProperty('credential_ref')

    const restore = {
      ...initialSecurityRecoveryDraft,
      action: 'restore' as const,
      artifact_path: '/protected/recovery.hmbk',
      source_digest: digest,
    }
    expect(validateSecurityRecoveryDraft({ ...restore, source_digest: 'not-a-digest' })).toMatch(/exact 64-character/i)
    expect(securityRecoveryPlanBody(restore)).toEqual({
      artifact_path: '/protected/recovery.hmbk',
      source_digest: digest,
    })
    expect(securityRecoveryApplyDecision(plan, digest, false, Date.parse('2026-10-02T12:05:00Z')).reason).toMatch(/destructive/i)
    expect(securityRecoveryApplyDecision(plan, 'd'.repeat(64), true, Date.parse('2026-10-02T12:05:00Z')).reason).toMatch(/exact security plan/i)
    expect(securityRecoveryApplyDecision(plan, digest, true, Date.parse('2026-10-02T12:05:00Z'))).toEqual({ allowed: true, reason: '' })

    const unknown = { state: 'outcome_unknown', effect_state: 'unknown' } as HypermidSecurityReceiptWire
    expect(receiptNeedsRecovery(unknown)).toBe(true)
  })
})
