import { describe, expect, it } from 'vitest'
import { emptyHint } from './SkillProposals'
import type { SkillLadderReview } from '../../shared/data/api'


const review = (over: Partial<SkillLadderReview> = {}): SkillLadderReview => ({
  verdict: 'no_action',
  elapsed_ms: 4210,
  session_key: 'sess:1',
  detail: '',
  at: '2026-08-23T12:00:00+00:00',
  ...over,
})

describe('skill-proposals empty state', () => {
  it('says the reviewer has NOT RUN when there is no last pass', () => {
    const hint = emptyHint(null)
    expect(hint).toMatch(/has not run yet/i)
    expect(hint).toMatch(/corrected|tools/i)
  })

  it('says the reviewer RAN AND FOUND NOTHING, and calls that healthy', () => {
    const hint = emptyHint(review({ verdict: 'no_action' }))
    expect(hint).toMatch(/nothing worth proposing/i)
    expect(hint).toMatch(/healthy/i)
    expect(hint).not.toMatch(/has not run/i)
  })

  it('names a FAILED pass as a failure rather than an idle queue', () => {
    const hint = emptyHint(review({ verdict: 'provider_error' }))
    expect(hint).toMatch(/did not finish/i)
    expect(hint).toContain('provider_error')
    expect(hint).not.toMatch(/healthy/i)
  })

  it('treats an UNMAPPED verdict as something to look at, not as health', () => {
    const hint = emptyHint(review({ verdict: 'some_future_verdict' }))
    expect(hint).toMatch(/did not finish/i)
    expect(hint).toContain('some_future_verdict')
  })

  it('reports every healthy verdict the backend can emit as healthy', () => {
    for (const v of ['env_failure_claim', 'no_action', 'enqueue_skipped', 'filed', 'template_filed', 'template_declined']) {
      expect(emptyHint(review({ verdict: v })), v).toMatch(/nothing worth proposing/i)
    }
  })

  it('three states produce three distinct sentences', () => {
    const sentences = new Set([
      emptyHint(null),
      emptyHint(review({ verdict: 'no_action' })),
      emptyHint(review({ verdict: 'provider_error' })),
    ])
    expect(sentences.size).toBe(3)
  })

  it('shows WHEN the pass ran, so a stale review is not read as a fresh one', () => {
    expect(emptyHint(review())).toContain(new Date('2026-08-23T12:00:00+00:00').toLocaleString())
  })
})
