import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { invalidateKeys } from '../../shared/data/data'
import { evidenceLabel } from './learningMeta'
import { LearningPage } from './LearningPage'
import { ApiError } from '../../shared/data/api'
import type { LearningInbox, LearningRow, StagingWeek } from '../../shared/data/api'


const row = (over: Partial<LearningRow> = {}): LearningRow => ({
  id: 'ablation.correction-heuristic', kind: 'retirement',
  title: 'Retire correction-heuristic — ablation measured no delta',
  provenance: 'inferred', source_cadence: 'ablation', source_excerpt: '',
  evidence_refs: ['ablation:ablation-20260817T120000Z', 'matrix:ablation-20260817T120000Z'],
  evidence_strength: 'ablation', reinforcements: 3, confidence: 0.7,
  manifest_valid: true, manifest_issues: [], risk_tier: 'review',
  status: 'pending', renderable: true, bulk_acceptable: true,
  gate: {
    state: 'ungated', reason: 'no gate run yet', before: null, after: null, delta: null,
    regressed: false, scenarios: 0, halted: false, dollars_est: 0, spend_observed: false,
    pin: {}, ran_at: '',
  },
  replay: {
    state: 'unreplayed', reason: 'no replay run yet', verdict: 'unmeasured',
    candidate_mean: null, baseline_mean: null, cases: 0, scored: 0, rejected: 0, tool_free: 0,
    deferred: false, provenance: [], ran_at: '',
  },
  ...over,
})

const inboxOf = (rows: LearningRow[]): LearningInbox => ({
  rows, total: rows.length, by_kind: {}, by_tier: {},
  flagged: 0, unrenderable: [], bulk_acceptable: rows.length,
})

const WEEK: StagingWeek = {
  days: 7, buckets: [], silent_days: [], error_days: [], produced_total: 0, cost_usd: 0,
}

const learningProposals = vi.fn<() => Promise<LearningInbox>>()
const learningStagingWeek = vi.fn<() => Promise<StagingWeek>>()
const learningHealth = vi.fn<() => Promise<never>>()
const judgeBench = vi.fn<() => Promise<never>>()
const evalStudies = vi.fn<() => Promise<never>>()
const retrievalBench = vi.fn<() => Promise<never>>()
const ablation = vi.fn<() => Promise<never>>()

vi.mock('../../shared/data/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      workflowAttention: () => Promise.resolve({ scopes: [] }),
      evalFieldMetrics: () => Promise.resolve({ subjects: [] }),
      learningProposals: () => learningProposals(),
      learningStagingWeek: () => learningStagingWeek(),
      learningHealth: () => learningHealth(),
      acceptLearningProposal: () => Promise.resolve({ ok: true }),
      rejectLearningProposal: () => Promise.resolve(undefined),
      judgeBench: () => judgeBench(),
      evalStudies: () => evalStudies(),
      retrievalBench: () => retrievalBench(),
      ablation: () => ablation(),
      identityReport: () => Promise.reject(new Error('not under test')),
      learningBenchmark: () => Promise.reject(
        new ApiError('No skill-impact benchmark has run yet.', 404, 'learning_benchmark_absent'),
      ),
    },
  }
})

describe('the evidence clause names the tier, not only the count', () => {
  it('distinguishes a measured ablation from a co-occurrence', () => {
    const measured = evidenceLabel(row())
    const correlated = evidenceLabel(row({ evidence_strength: 'correlated' }))
    expect(measured).toContain('measured on/off')
    expect(measured).not.toBe(correlated)
    expect(correlated).toContain('correlated')
    expect(measured).toContain('2 evidence refs')
  })

  it('names a controlled study and an anecdote as themselves', () => {
    expect(evidenceLabel(row({ evidence_strength: 'causal' }))).toContain('controlled study')
    expect(evidenceLabel(row({ evidence_strength: 'anecdotal' }))).toContain('anecdotal')
  })

  it('reads an UNGRADED tier as ungraded, never as a grade', () => {
    expect(evidenceLabel(row({ evidence_strength: '' }))).toContain('ungraded')
    expect(evidenceLabel(row({ evidence_strength: 'invented_tier' }))).toContain('ungraded')
    expect(evidenceLabel(row({ evidence_strength: '' }))).not.toContain('correlated')
  })

  it('says so when there is nothing to check', () => {
    expect(evidenceLabel(row({ evidence_refs: [], evidence_strength: 'ablation' })))
      .toBe('no evidence')
  })
})

describe('LearningPage RENDERS the grade (the call site)', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
    vi.clearAllMocks()
    learningStagingWeek.mockResolvedValue(WEEK)
    learningHealth.mockRejectedValue(new Error('not under test'))
    judgeBench.mockRejectedValue(new ApiError('No judge benchmark has run yet. Run `gideon judge-bench` to produce one.', 404, 'judge_bench_absent'))
    evalStudies.mockRejectedValue(new ApiError('No study is registered under that id.', 404, 'study_absent'))
    retrievalBench.mockRejectedValue(new ApiError('No retrieval benchmark has run yet. Run `gideon retrieval-eval` to score both stores.', 404, 'retrieval_absent'))
    ablation.mockRejectedValue(new ApiError('No ablation has run yet. Register a component in `evals/ablation_registry.json` and run `gideon ablation --force`.', 404, 'ablation_absent'))
  })

  it('paints the ablation grade on the row a reviewer decides on', async () => {
    const control = render(<div />)
    expect(screen.queryByText(/measured on\/off/)).toBeNull()
    control.unmount()

    learningProposals.mockResolvedValue(inboxOf([row()]))
    render(<LearningPage />)

    expect(await screen.findByText(/measured on\/off/)).toBeTruthy()
    expect(screen.getByText(/Retire correction-heuristic/)).toBeTruthy()
  })

  it('does not upgrade an ungraded row on the page either', async () => {
    learningProposals.mockResolvedValue(inboxOf([row({ evidence_strength: '' })]))
    render(<LearningPage />)

    expect(await screen.findByText(/ungraded/)).toBeTruthy()
    expect(screen.queryByText(/measured on\/off/)).toBeNull()
  })
})
