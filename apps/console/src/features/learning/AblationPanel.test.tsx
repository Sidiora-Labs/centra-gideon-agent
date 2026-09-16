import { describe, expect, it, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { invalidateKeys } from '../../shared/data/data'
import { AblationPanel } from './AblationPanel'
import { LearningPage } from './LearningPage'
import { ApiError } from '../../shared/data/api'
import type { AblationArmAggregate, AblationView, LearningInbox, StagingWeek } from '../../shared/data/api'


const learningProposals = vi.fn<() => Promise<LearningInbox>>()
const learningStagingWeek = vi.fn<() => Promise<StagingWeek>>()
const learningHealth = vi.fn<() => Promise<never>>()
const judgeBench = vi.fn<() => Promise<never>>()
const evalStudies = vi.fn<() => Promise<never>>()
const retrievalBench = vi.fn<() => Promise<never>>()
const identityReport = vi.fn<() => Promise<never>>()
const ablation = vi.fn<() => Promise<AblationView>>()
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
      judgeBench: () => judgeBench(),
      evalStudies: () => evalStudies(),
      retrievalBench: () => retrievalBench(),
      identityReport: () => identityReport(),
      ablation: () => ablation(),
      learningBenchmark: () => learningBenchmark(),
      acceptLearningProposal: () => Promise.resolve({ ok: true }),
      rejectLearningProposal: () => Promise.resolve(undefined),
    },
  }
})

const EMPTY_INBOX: LearningInbox = {
  rows: [], total: 0, by_kind: {}, by_tier: {}, flagged: 0, unrenderable: [], bulk_acceptable: 0,
}
const WEEK: StagingWeek = {
  days: 7, buckets: [], silent_days: [], error_days: [], produced_total: 0, cost_usd: 0,
}

function agg(over: Partial<AblationArmAggregate> = {}): AblationArmAggregate {
  return {
    counts: { passed: 3, failed: 0, verifier_absent: 0 },
    total: 3,
    scored_count: 3,
    mean_score: 0.82,
    ...over,
  }
}

function view(over: Partial<AblationView> = {}): AblationView {
  return {
    report: {
      component_id: 'refiner-skill',
      kind: 'skill',
      target: 'summarize-first',
      subject: 'inbox-triage',
      verdict: 'keep',
      arms: { on: agg(), off: agg({ mean_score: 0.4 }) },
      delta: 0.42,
      cheap_delta: null,
      epsilon: 0.05,
      matrix_id: 'ablation-20260824T000000Z',
      trials: 3,
      created_at: '2026-08-24T00:00:00+00:00',
      live_state: { 'config.json': 'sha256:abc' },
    },
    verdict_vocabulary: ['keep', 'remove', 'lighten'],
    registry: [],
    history: [],
    last_run_ts: '2026-08-24T00:00:00+00:00',
    cadence_days: 30,
    due: false,
    ...over,
  }
}

describe('the ablation report is CONSUMED, not merely served', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
    vi.clearAllMocks()
    learningProposals.mockResolvedValue(EMPTY_INBOX)
    learningStagingWeek.mockResolvedValue(WEEK)
    learningHealth.mockRejectedValue(new Error('not under test'))
    judgeBench.mockRejectedValue(new ApiError('No judge benchmark has run yet. Run `gideon judge-bench` to produce one.', 404, 'judge_bench_absent'))
    evalStudies.mockRejectedValue(new ApiError('No study is registered under that id.', 404, 'study_absent'))
    retrievalBench.mockRejectedValue(new ApiError('No retrieval benchmark has run yet. Run `gideon retrieval-eval` to score both stores.', 404, 'retrieval_absent'))
    identityReport.mockRejectedValue(new Error('not under test'))
    learningBenchmark.mockRejectedValue(new ApiError('No skill-impact benchmark has run yet. Run `python scripts/learning_benchmark.py --preflight` and then `--run`.', 404, 'learning_benchmark_absent'))
  })

  it('is rendered BY LearningPage, and the api client is actually called', async () => {
    const control = render(<div />)
    expect(screen.queryByText('Component ablation')).toBeNull()
    expect(ablation).not.toHaveBeenCalled()
    control.unmount()

    ablation.mockResolvedValue(view())
    render(<LearningPage />)

    expect(ablation).toHaveBeenCalled()
    expect(await screen.findByText('Component ablation')).toBeTruthy()
    expect(screen.getByText('ablation-20260824T000000Z')).toBeTruthy()
    expect(screen.getByText(/Keep summarize-first/)).toBeTruthy()
  })

  it('names the panel for assistive tech', () => {
    render(<AblationPanel view={view()} error={undefined} onRetry={() => {}} />)
    expect(screen.getByRole('region', { name: 'Component ablation' })).toBeTruthy()
  })

  it('renders an unmeasured arm as unmeasured, never as a zero', () => {
    render(<AblationPanel
      view={view({
        report: {
          ...view().report,
          verdict: 'inconclusive',
          arms: { on: agg(), off: agg({ counts: { verifier_absent: 3 }, scored_count: 0, mean_score: null }) },
          delta: null,
        },
      })}
      error={undefined}
      onRetry={() => {}}
    />)
    expect(screen.getAllByText('not measured').length).toBe(2)
    expect(screen.queryByText('0.000')).toBeNull()
    expect(screen.queryByText('+0.000')).toBeNull()
  })

  it('refuses to read an inconclusive report as a retirement', () => {
    render(<AblationPanel
      view={view({ report: { ...view().report, verdict: 'inconclusive', delta: null } })}
      error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('inconclusive')).toBeTruthy()
    expect(screen.getByText(/No verdict for summarize-first/)).toBeTruthy()
    expect(screen.queryByText(/Retire/)).toBeNull()
  })

  it('reads the verdict as a verdict, not as a raw enum', () => {
    render(<AblationPanel
      view={view({ report: { ...view().report, verdict: 'remove', delta: 0.01 } })}
      error={undefined} onRetry={() => {}} />)
    expect(screen.getByText(/Retire summarize-first/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Inbox' })).toBeTruthy()
    expect(screen.queryByText('remove')).toBeNull()
  })

  it('says a lighten verdict is unreachable for a component with no cheap form', () => {
    render(<AblationPanel
      view={view({
        registry: [{
          component_id: 'refiner-skill', kind: 'skill', target: 'summarize-first',
          subject: 'inbox-triage', off_value: false, cheap_value: null, live_refs: [],
          description: 'the refiner summarizes before filing',
        }],
      })}
      error={undefined} onRetry={() => {}} />)
    expect(screen.getByText(/No cheap form declared/)).toBeTruthy()
  })

  it('shows a remove verdict that filed NOTHING as unfiled', () => {
    render(<AblationPanel
      view={view({
        history: [
          { ts: '2026-07-24T00:00:00+00:00', component_id: 'refiner-skill', verdict: 'remove', matrix_id: 'm1', delta: 0.001, proposal: '' },
          { ts: '2026-06-24T00:00:00+00:00', component_id: 'other', verdict: 'remove', matrix_id: 'm0', delta: 0.002, proposal: 'not_filed:cooldown' },
        ],
      })}
      error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('not filed')).toBeTruthy()
    expect(screen.getByText('not filed (cooldown)')).toBeTruthy()
  })


  it('renders "no ablation yet" as guidance rather than as a load failure', () => {
    render(<AblationPanel view={undefined} error={new ApiError('No ablation has run yet. Register a component in `evals/ablation_registry.json` and run `gideon ablation --force`.', 404, 'ablation_absent')} onRetry={() => {}} />)
    expect(screen.getByText(/gideon ablation --force/)).toBeTruthy()
    expect(screen.queryByText(/Retry/)).toBeNull()
  })

  it('surfaces a REAL failure instead of rendering it as "nothing has run"', () => {
    render(<AblationPanel view={undefined} error={new Error('boom')} onRetry={() => {}} />)
    expect(screen.getByText(/ablation report/)).toBeTruthy()
    expect(screen.queryByText(/gideon ablation --force/)).toBeNull()
    expect(screen.queryByText(/No ablation has run yet/)).toBeNull()
  })

  it('points at the SWITCH when the substrate is off, not at the registry', () => {
    render(<AblationPanel view={undefined} error={new ApiError('The eval substrate is off. Turn on `evals.enabled` to publish benchmark results.', 404, 'evals_disabled')} onRetry={() => {}} />)
    expect(screen.getByRole('link', { name: /Evals enabled/ }).getAttribute('href')).toBe('#/settings/evals')
    expect(screen.queryByText(/ablation_registry.json/)).toBeNull()
  })

  it('the api client targets GET /api/evals/ablation', () => {
    const src = readFileSync(join(process.cwd(), "src/shared/data/api.ts"), 'utf8')
    expect(src).toContain("ablation: () => get<AblationView>('/api/evals/ablation')")
    expect(src).not.toContain("'/api/evals/ablations'")
  })
})
