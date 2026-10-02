import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { HypermidEffectReviewPlanWire, HypermidEffectsWire } from '../../shared/data/api'
import { UnknownEffectsView } from './UnknownEffects'

const scope = { owner_id: 'owner', project_id: 'project' }
const snapshot: HypermidEffectsWire = {
  scope,
  checked_at_ms: Date.parse('2026-10-02T12:00:00Z'),
  effects: [{
    effect_id: 'effect-1', module_id: 'module-1', operation: 'publish_message', principal_id: 'principal-1', scope,
    input_digest: 'a'.repeat(64), created_ms: Date.parse('2026-10-02T11:59:00Z'), state: 'unknown', reviewable: false,
    result_digest: null, reason: 'provider acknowledgement was lost', settled_ms: Date.parse('2026-10-02T11:59:01Z'),
    next_action: 'check_authoritative_status', review_plan: null,
  }],
}
const plan: HypermidEffectReviewPlanWire = {
  review_id: 'effect-review-1', effect_id: 'effect-1', idempotency_key: 'do-not-render-idempotency', module_id: 'module-1', operation: 'publish_message', scope,
  input_digest: 'a'.repeat(64), provider_id: 'do-not-render-provider', provider_proof_id: 'do-not-render-proof-id',
  provider_proof_digest: 'b'.repeat(64), proposed_state: 'not_started', result_digest: null, reason: 'provider confirms dispatch did not start',
  created_ms: Date.parse('2026-10-02T12:01:00Z'), plan_digest: 'c'.repeat(64),
}

describe('Hypermid unknown effects', () => {
  it('keeps ambiguous effects blocked and reviews only digest evidence without a repeat control', () => {
    const html = renderToStaticMarkup(<UnknownEffectsView snapshot={snapshot} plan={plan} onRefresh={vi.fn()} onReview={vi.fn()}
      onReviewed={vi.fn()} onCancel={vi.fn()} onReconcile={vi.fn()} />)
    expect(html).toContain('Await authoritative provider status')
    expect(html).toContain('does not repeat the external effect')
    expect(html).toContain(`Provider proof digest ${'b'.repeat(64)}`)
    expect(html).toContain(`Plan digest ${'c'.repeat(64)}`)
    expect(html).not.toContain('do-not-render-provider')
    expect(html).not.toContain('do-not-render-proof-id')
    expect(html).not.toContain('do-not-render-idempotency')
    expect(html.toLowerCase()).not.toContain('retry')
  })
})
