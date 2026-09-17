import { describe, expect, it, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { invalidateKeys } from '../../shared/data/data'
import { BenchmarkPanel } from './BenchmarkPanel'
import { LearningPage } from './LearningPage'
import { ApiError } from '../../shared/data/api'
import type {
  BenchmarkReport, BenchmarkTaskRow, BenchmarkView, LearningInbox, StagingWeek,
} from '../../shared/data/api'


const learningProposals = vi.fn<() => Promise<LearningInbox>>()
const learningStagingWeek = vi.fn<() => Promise<StagingWeek>>()
const learningHealth = vi.fn<() => Promise<never>>()
const judgeBench = vi.fn<() => Promise<never>>()
const evalStudies = vi.fn<() => Promise<never>>()
const retrievalBench = vi.fn<() => Promise<never>>()
const ablation = vi.fn<() => Promise<never>>()
const identityReport = vi.fn<() => Promise<never>>()
const learningBenchmark = vi.fn<() => Promise<BenchmarkView>>()

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
      ablation: () => ablation(),
      identityReport: () => identityReport(),
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
const DOC = 'docs/reference/LEARNING_BENCHMARK_PROTOCOL.md'

const BINDING = {
  use_case: 'chat',
  provider_name: 'LocalOllama',
  model: 'gemma4:12b',
  protocol: 'openai',
  base_url: 'http://127.0.0.1:11434/v1',
  api_key_env: '',
  max_tokens: null,
}

function arm(mean: number, spread = 1.5) {
  return { trials: 5, mean_score: mean, spread, tokens: 41000, tokens_per_point: 512.5 }
}

function task(over: Partial<BenchmarkTaskRow> = {}): BenchmarkTaskRow {
  return {
    task_id: 'sk_grill',
    skill: 'grill',
    verdict: 'skills_on_wins',
    verdict_class: 'skills_on_wins',
    reason: '',
    delta_points: 12.5,
    token_ratio: 1.002,
    arms: { skills_on: arm(78), skills_off: arm(65.5) },
    absent_cells: 0,
    tool_calls: { skills_on: 6, skills_off: 2 },
    spend_observed: true,
    spend_estimated: false,
    tokens_recorded: true,
    unrecorded_spend_cells: 0,
    notes: [],
    ...over,
  }
}

function report(over: Partial<BenchmarkReport> = {}): BenchmarkReport {
  return {
    run_id: 'learnbench-20260826T000000Z',
    report_schema: 2,
    tokens_recorded: true,
    unrecorded_spend_cells: 0,
    created_at: '2026-08-26T00:00:00+00:00',
    protocol_doc: DOC,
    task_set_version: 1,
    task_set_fingerprint: { sk_grill: 'ab'.repeat(32) },
    trials_per_arm: 5,
    arms: ['skills_on', 'skills_off'],
    thresholds: {
      inconclusive_band_points: 5,
      token_match_tolerance: 0.05,
      min_trials_per_arm: 3,
      source: 'shared/replay/fanout_measure.py',
    },
    tasks: [task()],
    skipped: [],
    measured_tasks: 1,
    absent_cells: 0,
    ...over,
  }
}

function view(over: Partial<BenchmarkView> = {}): BenchmarkView {
  return {
    report: report(),
    register: [{ task_id: 'sk_grill', skill: 'grill', observable: 'no bare acceptance' }],
    task_set_version: 1,
    protocol_doc: DOC,
    stated_variance: ['same task_set_version'],
    ...over,
  }
}

describe('the skill-impact benchmark is CONSUMED, not merely served', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
    vi.clearAllMocks()
    learningProposals.mockResolvedValue(EMPTY_INBOX)
    learningStagingWeek.mockResolvedValue(WEEK)
    learningHealth.mockRejectedValue(new Error('not under test'))
    judgeBench.mockRejectedValue(new ApiError('no judge bench', 404, 'judge_bench_absent'))
    evalStudies.mockRejectedValue(new ApiError('no study', 404, 'study_absent'))
    retrievalBench.mockRejectedValue(new ApiError('no retrieval bench', 404, 'retrieval_absent'))
    ablation.mockRejectedValue(new ApiError('no ablation', 404, 'ablation_absent'))
    identityReport.mockRejectedValue(new Error('not under test'))
  })

  it('is rendered BY LearningPage, and the api client is actually called', async () => {
    const control = render(<div />)
    expect(screen.queryByText('Skill impact benchmark')).toBeNull()
    expect(learningBenchmark).not.toHaveBeenCalled()
    control.unmount()

    learningBenchmark.mockResolvedValue(view())
    render(<LearningPage />)

    expect(learningBenchmark).toHaveBeenCalled()
    expect(await screen.findByText('Skill impact benchmark')).toBeTruthy()
    expect(screen.getByText('learnbench-20260826T000000Z')).toBeTruthy()
    expect(screen.getByText('+12.50')).toBeTruthy()
  })

  it('names the panel for assistive tech', () => {
    render(<BenchmarkPanel view={view()} error={undefined} onRetry={() => {}} />)
    expect(screen.getByRole('region', { name: 'Skill impact benchmark' })).toBeTruthy()
  })


  it('renders an unmeasured task as "not measured", never as 0.000', () => {
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            tasks: [task({
              verdict: null,
              verdict_class: null,
              delta_points: null,
              token_ratio: null,
              arms: {},
              absent_cells: 10,
              reason: 'arm skills_off produced no scored cell',
            })],
            measured_tasks: 0,
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Nothing was measured')).toBeTruthy()
    expect(screen.getByText('arm skills_off produced no scored cell')).toBeTruthy()
    expect(screen.getAllByText('not measured').length).toBeGreaterThan(0)
    expect(screen.queryByText('+0.00')).toBeNull()
    expect(screen.queryByText('0.000')).toBeNull()
  })

  it('names BOTH arms, pluralised, when neither produced a scored cell', () => {
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            tasks: [task({
              verdict: null,
              verdict_class: null,
              delta_points: null,
              token_ratio: null,
              arms: {},
              absent_cells: 10,
              reason: 'arms skills_on, skills_off produced no scored cell',
            })],
            measured_tasks: 0,
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('arms skills_on, skills_off produced no scored cell')).toBeTruthy()
    expect(screen.queryByText(/arm\(s\)/)).toBeNull()
    expect(screen.queryByText('0.00 ±0.00')).toBeNull()
  })

  it('draws a real measured delta, so the case above is not vacuous', () => {
    render(<BenchmarkPanel view={view()} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('+12.50')).toBeTruthy()
    expect(screen.getByText('78.00 ±1.50')).toBeTruthy()
    expect(screen.getByText('1 of 1 task measured')).toBeTruthy()
    expect(screen.queryByText('not measured')).toBeNull()
  })

  it('publishes a skills-off win with the same prominence as a win', () => {
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            tasks: [task({
              verdict: 'skills_off_wins',
              verdict_class: 'skills_off_wins',
              delta_points: -9.25,
            })],
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('skills_off_wins')).toBeTruthy()
    expect(screen.getByText('-9.25')).toBeTruthy()
  })

  it('says so when spend was not observed, rather than printing a bare token ratio', () => {
    render(
      <BenchmarkPanel
        view={view({ report: report({ tasks: [task({ spend_observed: false })] }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(/spend not observed/)).toBeTruthy()
  })

  it('says "estimated" about tokens, because §4 requires the word', () => {
    render(
      <BenchmarkPanel
        view={view({ report: report({ tasks: [task({ spend_estimated: true })] }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(/estimated, not provider-reported/)).toBeTruthy()
  })

  it('reports tasks the runner refused to run instead of shortening the table', () => {
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            tasks: [],
            measured_tasks: 0,
            skipped: [{
              task_id: 'sk_grill',
              skill: 'grill',
              blockers: ['incomplete RunPin (missing: model_fingerprint)'],
            }],
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Not run (1)')).toBeTruthy()
    expect(screen.getByText(/incomplete RunPin/)).toBeTruthy()
  })


  it('distinguishes "no benchmark yet" from "we could not ask"', () => {
    const absent = render(
      <BenchmarkPanel
        view={undefined}
        error={new ApiError('no benchmark yet', 404, 'learning_benchmark_absent')}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(/No skill-impact benchmark has run yet/)).toBeTruthy()
    expect(screen.getByRole('link', { name: /Methodology/ })).toBeTruthy()
    absent.unmount()

    render(
      <BenchmarkPanel view={undefined} error={new Error('boom')} onRetry={() => {}} />,
    )
    expect(screen.queryByText(/No skill-impact benchmark has run yet/)).toBeNull()
    expect(screen.getByText(/skill-impact benchmark/)).toBeTruthy()
  })

  it('tells a user the substrate is off rather than showing an empty benchmark', () => {
    render(
      <BenchmarkPanel view={undefined} error={new ApiError('evals are off', 404, 'evals_disabled')} onRetry={() => {}} />,
    )
    expect(screen.getByText(/The eval substrate is off/)).toBeTruthy()
    expect(screen.queryByText(/No skill-impact benchmark has run yet/)).toBeNull()
  })


  it('links the methodology at the path the REPORT cited, not a hardcoded one', () => {
    render(
      <BenchmarkPanel
        view={view({ report: report({ protocol_doc: 'docs/other/protocol-v2.md' }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    const link = screen.getByRole('link', { name: /Methodology/ }) as HTMLAnchorElement
    expect(link.href).toContain('docs/other/protocol-v2.md')
    expect(link.href).not.toContain('LEARNING_BENCHMARK_PROTOCOL.md')
  })

  it('prints the reproduction conditions and cites where the variance is stated', () => {
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            reproduction: {
              baseline_run_id: 'learnbench-A',
              rerun_run_id: 'learnbench-B',
              reproduces: false,
              stated_variance: ['same task_set_version', 'same verdict class per task'],
              stated_variance_source: `${DOC} §8 (Reproduction (V4))`,
              conditions: {
                'same task_set_version': true,
                'same verdict class per task': false,
              },
              verdict_changes: [
                { task_id: 'sk_grill', baseline: 'inconclusive', rerun: 'skills_on_wins' },
              ],
              notes: [],
            },
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Did NOT reproduce within the stated variance')).toBeTruthy()
    expect(screen.getByText(`${DOC} §8 (Reproduction (V4))`)).toBeTruthy()
    expect(screen.getByText(/same verdict class per task/)).toBeTruthy()
    expect(screen.getByText('not met')).toBeTruthy()
    expect(screen.getByText('met')).toBeTruthy()
  })

  it('omits the reproduction block entirely when no re-run was judged', () => {
    render(<BenchmarkPanel view={view()} error={undefined} onRetry={() => {}} />)
    expect(screen.queryByText(/reproduce within the stated variance/)).toBeNull()
  })


  it('names the model the cells actually called', () => {
    render(
      <BenchmarkPanel
        view={view({ report: report({ provider_binding: BINDING }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Cells called LocalOllama:gemma4:12b')).toBeTruthy()
    expect(screen.getByText('http://127.0.0.1:11434/v1')).toBeTruthy()
    expect(screen.queryByText(/not a model measurement/)).toBeNull()
    expect(screen.queryByText(/Provenance was not recorded/)).toBeNull()
  })

  it('says an unbound run measured no model, instead of publishing a bare table', () => {
    render(
      <BenchmarkPanel
        view={view({ report: report({ provider_binding: null }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('No model was bound — these cells called no model')).toBeTruthy()
    expect(screen.getByText('not a model measurement')).toBeTruthy()
    expect(screen.getByText(/resolved the offline/)).toBeTruthy()
    expect(screen.queryByText(/Cells called/)).toBeNull()
  })

  it('distinguishes an UNRECORDED provenance from a recorded absence of one', () => {
    const legacy = report({ report_schema: 1 })
    expect('provider_binding' in legacy).toBe(false)
    const unrecorded = render(
      <BenchmarkPanel view={view({ report: legacy })} error={undefined} onRetry={() => {}} />,
    )
    expect(screen.getByText('Provenance was not recorded')).toBeTruthy()
    expect(screen.getByText('provenance unrecorded')).toBeTruthy()
    expect(screen.queryByText('No model was bound — these cells called no model')).toBeNull()
    expect(screen.queryByText('not a model measurement')).toBeNull()
    unrecorded.unmount()

    render(
      <BenchmarkPanel
        view={view({ report: report({ provider_binding: null }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.queryByText('Provenance was not recorded')).toBeNull()
    expect(screen.getByText('No model was bound — these cells called no model')).toBeTruthy()
  })

  it('reads provenance from the STATED schema, not from whether the key is present', () => {
    const stale = render(
      <BenchmarkPanel
        view={view({ report: report({ report_schema: 1, provider_binding: null }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Provenance was not recorded')).toBeTruthy()
    expect(screen.queryByText('No model was bound — these cells called no model')).toBeNull()
    stale.unmount()

    render(
      <BenchmarkPanel
        view={view({ report: report({ report_schema: undefined, provider_binding: null }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Provenance was not recorded')).toBeTruthy()
  })

  it('renders what the CELLS could reach beside what the HOME was bound to', () => {
    const bound = render(
      <BenchmarkPanel
        view={view({
          report: report({
            provider_binding: BINDING,
            pin: {
              model_fp: '5970c589da34',
              model_fingerprint: { chat: 'LocalOllama:gemma4:12b', reasoning: 'LocalOllama:gemma4:12b' },
              cell_model_fp: '709d87c51b62',
              cell_model_fingerprint: { chat: 'LocalOllama:gemma4:12b' },
            },
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(/could reach/)).toBeTruthy()
    expect(screen.getByText(/not what the cells reached/)).toBeTruthy()
    bound.unmount()

    const none = render(
      <BenchmarkPanel
        view={view({ report: report({ pin: { cell_model_fingerprint: {} } }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(/no model at all/)).toBeTruthy()
    expect(screen.queryByText(/not recorded\./)).toBeNull()
    none.unmount()

    render(
      <BenchmarkPanel
        view={view({ report: report({ pin: { cell_model_fingerprint: null } }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(/not recorded/)).toBeTruthy()
    expect(screen.queryByText(/no model at all/)).toBeNull()
  })

  it('renders an unrecorded token ratio as "not recorded", not as "not measured" or 0.0000', () => {
    const unrecorded = render(
      <BenchmarkPanel
        view={view({
          report: report({
            tokens_recorded: false,
            unrecorded_spend_cells: 3,
            tasks: [
              task({
                verdict: 'tokens_unrecorded',
                verdict_class: 'tokens_unrecorded',
                token_ratio: null,
                tokens_recorded: false,
                unrecorded_spend_cells: 3,
                reason: '3 contributing cell(s) reported no token usage',
              }),
            ],
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('tokens_unrecorded')).toBeTruthy()
    expect(screen.queryByText('not measured')).toBeNull()
    expect(screen.getAllByText('not recorded').length).toBe(2)
    expect(screen.queryByText('0.0000')).toBeNull()
    expect(screen.getByText('+12.50')).toBeTruthy()
    expect(screen.getByText(/every token ratio below is/)).toBeTruthy()
    unrecorded.unmount()

    const zero = render(
      <BenchmarkPanel
        view={view({ report: report({ tasks: [task({ token_ratio: 0 })] }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('0.0000')).toBeTruthy()
    expect(screen.queryByText('not recorded')).toBeNull()
    zero.unmount()

    render(
      <BenchmarkPanel
        view={view({
          report: report({ tasks: [task({ verdict: null, token_ratio: null, delta_points: null })] }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getAllByText('not measured').length).toBeGreaterThan(0)
    expect(screen.queryByText('not recorded')).toBeNull()
  })

  it('labels the pin as the HOME’s binding, not as what the cells reached', () => {
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            provider_binding: null,
            pin: { model_fp: '5970c589da34', model_fingerprint: { chat: 'LocalOllama:gemma4:12b' } },
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(/chat=LocalOllama:gemma4:12b/)).toBeTruthy()
    expect(screen.getByText(/not what the cells reached/)).toBeTruthy()
    expect(screen.getByText('No model was bound — these cells called no model')).toBeTruthy()
    expect(screen.queryByText('Cells called LocalOllama:gemma4:12b')).toBeNull()
  })


  it('publishes the note that says WHY a direction was withheld', () => {
    const note = 'token spend differs by 13.7% (skills_on 42517 vs skills_off 37409), over the 5% '
      + 'match tolerance — the arms are not spend-matched, so no direction is offered.'
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            tasks: [task({
              verdict: 'not_token_matched',
              verdict_class: 'not_token_matched',
              delta_points: 0,
              token_ratio: 1.1365,
              notes: [note],
            })],
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(note)).toBeTruthy()
    expect(screen.getByText('1.1365')).toBeTruthy()
  })

  it('does not print the estimated/unobserved notes twice', () => {
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            tasks: [task({
              spend_estimated: true,
              notes: ['tokens and dollars are ESTIMATED, not provider-reported'],
            })],
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getAllByText(/estimated, not provider-reported/i)).toHaveLength(1)
  })

  it('shows a zero token ratio as a zero, not as "not measured"', () => {
    const zero = render(
      <BenchmarkPanel
        view={view({ report: report({ tasks: [task({ token_ratio: 0 })] }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('0.0000')).toBeTruthy()
    zero.unmount()

    render(
      <BenchmarkPanel
        view={view({ report: report({ tasks: [task({ token_ratio: null })] }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.queryByText('0.0000')).toBeNull()
    expect(screen.getAllByText('not measured').length).toBeGreaterThan(0)
  })

  it('renders provenance for a report with no pin at all, without inventing one', () => {
    render(
      <BenchmarkPanel
        view={view({ report: report({ provider_binding: BINDING }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Cells called LocalOllama:gemma4:12b')).toBeTruthy()
    expect(screen.queryByText(/the pin records this/i)).toBeNull()
  })
})
