import { describe, expect, it } from 'vitest'
import {
  containsCredentialValue,
  modelIsReady,
  openRevisionedDraft,
  reapplyRevisionedDraft,
  rejectRevisionedDraft,
  reloadRevisionedDraft,
  type HypermidRuntimeConfigValue,
} from './configState'

const runtime: HypermidRuntimeConfigValue = {
  mode: 'pass_through',
  overflow_policy: 'reclaim_then_refuse',
  refusal_policy: 'refuse',
  features: {
    background_summaries: false,
    reduction_tools: false,
    automatic_reclaim: false,
    nudges: false,
    subagent_contributions: false,
    synthetic_hook_blocks: false,
  },
}

describe('Hypermid configuration state', () => {
  it('keeps mode and strictness separate and preserves the original revision with a refused draft', () => {
    const opened = openRevisionedDraft(runtime, 'digest-a', 4)
    const edited = {
      ...opened,
      draft: { ...opened.draft, mode: 'shadow' as const, refusal_policy: 'host_passthrough' as const },
    }
    const current = { ...runtime, mode: 'off' as const }
    const refused = rejectRevisionedDraft(edited, current, 'digest-b', 'Changed elsewhere', 5)

    expect(refused.draft).toMatchObject({ mode: 'shadow', refusal_policy: 'host_passthrough' })
    expect(refused.revision).toBe('digest-a')
    expect(reloadRevisionedDraft(refused)).toEqual(openRevisionedDraft(current, 'digest-b', 5))
    expect(reapplyRevisionedDraft(refused)).toMatchObject({
      base: current,
      draft: edited.draft,
      revision: 'digest-b',
      policy_revision: 5,
    })
  })

  it('requires an observed healthy probe before calling a model ready', () => {
    expect(modelIsReady({ availability: 'available', health: 'healthy', last_probe_at: null })).toBe(false)
    expect(modelIsReady({ availability: 'available', health: 'unknown', last_probe_at: '2026-10-02T00:00:00Z' })).toBe(false)
    expect(modelIsReady({ availability: 'available', health: 'healthy', last_probe_at: '2026-10-02T00:00:00Z' })).toBe(true)
  })

  it('refuses secret-bearing fields while accepting credential presence metadata', () => {
    expect(containsCredentialValue({ credentials: [{ name: 'MODEL_KEY', present: true, consumers: ['embeddings'] }] })).toBe(false)
    expect(containsCredentialValue({ credentials: [{ name: 'MODEL_KEY', present: true, metadata: { token: 'reusable' } }] })).toBe(true)
    expect(containsCredentialValue({ value: 'secret' })).toBe(true)
  })
})
