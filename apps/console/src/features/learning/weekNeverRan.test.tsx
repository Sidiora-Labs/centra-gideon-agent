import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { invalidateKeys } from '../../shared/data/data'
import { LearningPage } from './LearningPage'
import { ApiError } from '../../shared/data/api'
import type { LearningInbox, StagingWeek } from '../../shared/data/api'


const week = (over: Partial<StagingWeek> = {}): StagingWeek => ({
  days: 7,
  buckets: [],
  silent_days: ['2026-08-29', '2026-08-30', '2026-08-31', '2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04'],
  error_days: [],
  produced_total: 0,
  cost_usd: 0,
  first_pass_day: null,
  ...over,
})

const EMPTY_INBOX: LearningInbox = {
  rows: [], total: 0, by_kind: {}, by_tier: {}, flagged: 0, unrenderable: [], bulk_acceptable: 0,
}

const learningStagingWeek = vi.fn<() => Promise<StagingWeek>>()

vi.mock('../../shared/data/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      workflowAttention: () => Promise.resolve({ scopes: [] }),
      evalFieldMetrics: () => Promise.resolve({ subjects: [] }),
      learningProposals: () => Promise.resolve(EMPTY_INBOX),
      learningStagingWeek: () => learningStagingWeek(),
      learningHealth: () => Promise.reject(new Error('not under test')),
      acceptLearningProposal: () => Promise.resolve({ ok: true }),
      rejectLearningProposal: () => Promise.resolve(undefined),
      judgeBench: () => Promise.reject(new ApiError('No judge benchmark has run yet.', 404, 'judge_bench_absent')),
      evalStudies: () => Promise.reject(new ApiError('No study is registered under that id.', 404, 'study_absent')),
      retrievalBench: () => Promise.reject(new ApiError('No retrieval benchmark has run yet.', 404, 'retrieval_absent')),
      ablation: () => Promise.reject(new ApiError('No ablation has run yet.', 404, 'ablation_absent')),
      identityReport: () => Promise.reject(new Error('not under test')),
      learningBenchmark: () => Promise.reject(
        new ApiError('No skill-impact benchmark has run yet.', 404, 'learning_benchmark_absent'),
      ),
    },
  }
})

describe('the silent-days chip uses the server classification', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
    vi.clearAllMocks()
  })

  it('renders the server-classified silent days even without a first pass', async () => {
    learningStagingWeek.mockResolvedValue(week())
    render(<LearningPage />)

    expect(await screen.findByText('Capture, last 7 days')).toBeTruthy()
    expect(screen.getByText(/7 silent/)).toBeTruthy()
  })

  it('keeps the chip for ran-then-died — silent days after a first run are the signal', async () => {
    learningStagingWeek.mockResolvedValue(week({ first_pass_day: '2026-08-01' }))
    render(<LearningPage />)

    expect(await screen.findByText(/7 silent/)).toBeTruthy()
    expect(screen.queryByText(/no capture pass has run yet/)).toBeNull()
  })

  it('keeps every server-classified silent day', async () => {
    learningStagingWeek.mockResolvedValue(week({
      first_pass_day: '2026-09-03',
      silent_days: ['2026-08-29', '2026-09-02', '2026-09-03', '2026-09-04'],
    }))
    render(<LearningPage />)

    expect(await screen.findByText(/4 silent/)).toBeTruthy()
  })
})
