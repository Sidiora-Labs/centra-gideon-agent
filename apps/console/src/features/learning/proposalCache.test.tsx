import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, renderHook, act, waitFor } from '@testing-library/react'
import { useQuery, invalidateKeys, peekQuery } from '../../shared/data/data'
import { PROPOSALS_KEY_PREFIX, WEEK_KEY, proposalsKey, refreshAfterDecision, refreshEverything } from './proposalCache'
import { LearningPage } from './LearningPage'
import { ApiError } from '../../shared/data/api'
import type { LearningInbox, LearningRow, StagingWeek } from '../../shared/data/api'


const row = (over: Partial<LearningRow> = {}): LearningRow => ({
  id: 'skill-f6fab94955e7', kind: 'skill', title: 'summarize before filing', provenance: 'refiner',
  source_cadence: 'run_end', source_excerpt: '', evidence_refs: ['r1'],
  evidence_strength: 'correlated',
  reinforcements: 2, confidence: 0.7, manifest_valid: true, manifest_issues: [],
  risk_tier: 'low', status: 'pending', renderable: true, bulk_acceptable: true,
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
  rows, total: rows.length,
  by_kind: rows.reduce<Record<string, number>>((a, r) => ({ ...a, [r.kind]: (a[r.kind] ?? 0) + 1 }), {}),
  by_tier: {}, flagged: 0, unrenderable: [], bulk_acceptable: rows.length,
})

const WEEK: StagingWeek = {
  days: 7, buckets: [], silent_days: [], error_days: [], produced_total: 3, cost_usd: 0,
}

const learningProposals = vi.fn<() => Promise<LearningInbox>>()
const learningStagingWeek = vi.fn<() => Promise<StagingWeek>>()
const acceptLearningProposal = vi.fn<() => Promise<{ ok: boolean }>>()
const rejectLearningProposal = vi.fn<() => Promise<void>>()
const learningHealth = vi.fn<() => Promise<never>>()
const judgeBench = vi.fn<() => Promise<never>>()
const evalStudies = vi.fn<() => Promise<never>>()
const retrievalBench = vi.fn<() => Promise<never>>()
const ablation = vi.fn<() => Promise<never>>()
const identityReport = vi.fn<() => Promise<never>>()
const learningBenchmark = vi.fn<() => Promise<never>>()

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
      acceptLearningProposal: () => acceptLearningProposal(),
      rejectLearningProposal: () => rejectLearningProposal(),
      judgeBench: () => judgeBench(),
      evalStudies: () => evalStudies(),
      retrievalBench: () => retrievalBench(),
      ablation: () => ablation(),
      identityReport: () => identityReport(),
      learningBenchmark: () => learningBenchmark(),
    },
  }
})

describe('LearningPage drops a decided row from the screen (#676)', () => {
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
    identityReport.mockRejectedValue(new Error('not under test'))
    learningBenchmark.mockRejectedValue(new ApiError('No skill-impact benchmark has run yet. Run `python scripts/learning_benchmark.py --preflight` and then `--run`.', 404, 'learning_benchmark_absent'))
    acceptLearningProposal.mockResolvedValue({ ok: true })
    rejectLearningProposal.mockResolvedValue(undefined)
  })

  async function decideOnly(label: 'Accept' | 'Reject') {
    learningProposals
      .mockResolvedValueOnce(inboxOf([row()]))
      .mockResolvedValue(inboxOf([]))

    const { findByText, getByText, queryByText } = render(<LearningPage />)
    const title = await findByText('summarize before filing')
    expect(title).toBeInTheDocument()

    await act(async () => { getByText(label).click() })
    await waitFor(() => expect(queryByText('summarize before filing')).not.toBeInTheDocument())
    return { getByText, queryByText }
  }

  it('removes the row after Reject and shows the empty state', async () => {
    const { getByText } = await decideOnly('Reject')
    expect(getByText('Nothing to review')).toBeInTheDocument()
    expect(rejectLearningProposal).toHaveBeenCalledTimes(1)
    expect(learningProposals.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('removes the row after Accept too', async () => {
    await decideOnly('Accept')
    expect(acceptLearningProposal).toHaveBeenCalledTimes(1)
    expect(learningProposals.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('keeps the row when the decision FAILS, and says why', async () => {
    learningProposals.mockResolvedValue(inboxOf([row()]))
    rejectLearningProposal.mockRejectedValue(new Error('only a human reviewer may reject proposals'))

    const { findByText, getByText } = render(<LearningPage />)
    await findByText('summarize before filing')
    await act(async () => { getByText('Reject').click() })

    await waitFor(() => expect(getByText('only a human reviewer may reject proposals')).toBeInTheDocument())
    expect(getByText('summarize before filing')).toBeInTheDocument()
  })

  it('does not re-read the capture week on a decision', async () => {
    await decideOnly('Reject')
    expect(learningStagingWeek).toHaveBeenCalledTimes(1)
  })

  it('DOES re-read the capture week on an explicit Refresh', async () => {
    learningProposals.mockResolvedValue(inboxOf([row()]))
    const { findByText, getByText } = render(<LearningPage />)
    await findByText('summarize before filing')
    expect(learningStagingWeek).toHaveBeenCalledTimes(1)

    await act(async () => { getByText('Refresh').click() })
    await waitFor(() => expect(learningStagingWeek).toHaveBeenCalledTimes(2))
  })
})

describe('refreshAfterDecision sweeps every facet, not just the active one', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
  })

  it('refetches the live view instead of only arming the next mount', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(inboxOf([row()]))
      .mockResolvedValue(inboxOf([]))
    const { result } = renderHook(() => useQuery<LearningInbox>(proposalsKey('skill'), fetcher))
    await waitFor(() => expect(result.current.data?.total).toBe(1))

    act(() => { refreshAfterDecision(result.current.refresh) })
    await waitFor(() => expect(result.current.data?.total).toBe(0))
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('drops the OTHER facets too, so selecting a tab cannot paint a decided row', async () => {
    const skillFetch = vi.fn().mockResolvedValue(inboxOf([row()]))
    const allFetch = vi.fn().mockResolvedValue(inboxOf([row()]))
    const skill = await act(async () => {
      const active = renderHook(() => useQuery<LearningInbox>(proposalsKey('skill'), skillFetch))
      renderHook(() => useQuery<LearningInbox>(proposalsKey(''), allFetch))
      return active
    })
    expect(peekQuery(proposalsKey('skill'))).toBeTruthy()
    expect(peekQuery(proposalsKey(''))).toBeTruthy()

    expect(allFetch, 'the All facet fetched once on mount').toHaveBeenCalledTimes(1)
    await act(async () => { refreshAfterDecision(skill.result.current.refresh) })
    expect(allFetch, 'the swept facet re-read itself, unprompted').toHaveBeenCalledTimes(2)
    expect(peekQuery(proposalsKey('')), 'and holds a FRESH value, not a stale one').toBeTruthy()
    expect(peekQuery(proposalsKey('skill'))).toBeTruthy()
  })

  it('leaves the capture week cached, and refreshEverything does not', async () => {
    const weekFetch = vi.fn().mockResolvedValue(WEEK)
    const listFetch = vi.fn().mockResolvedValue(inboxOf([]))
    const { result } = renderHook(() => useQuery(WEEK_KEY, weekFetch))
    const list = renderHook(() => useQuery(proposalsKey(''), listFetch))
    await waitFor(() => expect(peekQuery(WEEK_KEY)).toBeTruthy())

    act(() => { refreshAfterDecision(list.result.current.refresh) })
    expect(peekQuery(WEEK_KEY)).toBeTruthy()

    act(() => { refreshEverything(list.result.current.refresh, result.current.refresh) })
    await waitFor(() => expect(weekFetch).toHaveBeenCalledTimes(2))
  })

  it('keys every facet under one prefix, so the sweep can find them', () => {
    for (const kind of ['', 'skill', 'lesson_batch', 'template_diff']) {
      expect(proposalsKey(kind).startsWith(PROPOSALS_KEY_PREFIX)).toBe(true)
    }
    expect(WEEK_KEY.startsWith(PROPOSALS_KEY_PREFIX)).toBe(false)
  })
})
