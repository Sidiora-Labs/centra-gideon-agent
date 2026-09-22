import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { WorkflowIntrospection } from '../../shared/data/api'
import { IntrospectPanel, riskyText, rowSummary } from './IntrospectPanel'
import { runCostText } from '../../shared/data/runCost'


let introspect: (id: string) => Promise<WorkflowIntrospection>

vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: { ...actual.api, workflowRunIntrospect: (id: string) => introspect(id) },
  }
})

function payload(over: Partial<WorkflowIntrospection> = {}): WorkflowIntrospection {
  const stats = {
    run_id: 'r1', tokens: 1200, tokens_recorded: true, cached_tokens: 100, cost_usd: 0.0342, priced: true,
    steps_completed: 4, steps_failed: 1, steps_cached: 1, duration_secs: 92.5,
    first_byte_ms: 830, models: ['claude-sonnet'], unverified_steps: 3,
    verification_debt: 0.75, cache_hit_rate: 0.2,
  }
  const proof = {
    summary: '4 steps completed, 1 failed, 1 served from cache',
    verified_steps: 1, total_steps: 4, coverage: 0.25,
    evidence_files: [],
    warnings: ['no evidence files were captured, so this section is a claim about the run rather than proof of it'],
    honest: true,
  }
  return {
    run_id: 'r1', workflow: 'weekly-report', stats,
    gates: {},
    edges: { branches: {}, judges: {} },
    template_card: {
      template: 'weekly-report', runs: 12, cost_p50: 0.03, cost_p95: 0.09, priced: true,
      duration_p50: 90, duration_p95: 240, failure_rate: 0.25, warnings: [],
    },
    proof,
    timeline: [
      { kind: 'run_started', ts: '2026-08-11T02:00:00+00:00', node_id: '', instance_path: '', state: '', model: '', detail: '' },
      { kind: 'step_completed', ts: '2026-08-11T02:00:10+00:00', node_id: 'draft', instance_path: 'main.draft', attempt: 2, state: 'done', duration_secs: 8, tokens: 900, cost_usd: 0.02, model: 'claude-sonnet', detail: '' },
    ],
    answers: {
      running: { status: 'complete', workflow: 'weekly-report', nodes: [] },
      changed: [],
      blocked: [],
      approval: [],
      failed: [{ node_id: 'publish' }],
      cost: stats,
      risky: { degraded: [], gates: [], edges: { branches: {}, judges: {} }, verification_debt: 0.75 },
      next: { action: 'nothing', detail: 'this run is complete', queued: [] },
      proof,
    },
    touched: [],
    checklist_gaps: [],
    ...over,
  }
}

beforeEach(() => {
  introspect = async () => payload()
})

describe('the nine questions reach the DOM', () => {
  it('renders every checklist question', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    for (const q of [
      /what is running now/i,
      /what changed/i,
      /what is blocked/i,
      /what needs my approval/i,
      /what failed/i,
      /what is costing money/i,
      /what is risky/i,
      /what happens next if I say nothing/i,
      /were the checks that passed real checks/i,
    ]) {
      expect(await screen.findByText(q)).toBeTruthy()
    }
  })

  it('shows the cost and latency strip with first-byte SEPARATE from duration', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText('~$0.0342')).toBeTruthy()
    expect(screen.getByText(/to first output/i)).toBeTruthy()
    expect(screen.getByText('830 ms')).toBeTruthy()
  })

  it('discloses an unrecorded token total rather than displaying zero', async () => {
    const base = payload()
    introspect = async () => ({
      ...base,
      stats: { ...base.stats, tokens: null, tokens_recorded: false },
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText('not recorded')).toBeTruthy()
    expect(screen.getByText(/did not record token usage/i)).toBeTruthy()
  })

  it('shows the template p50/p95 card, never a mean', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/cost p50/i)).toBeTruthy()
    expect(screen.getByText(/cost p95/i)).toBeTruthy()
    expect(screen.getByText(/across 12 runs/i)).toBeTruthy()
    expect(screen.queryByText(/average|mean/i)).toBeNull()
  })

  it('says a single-run card IS that run rather than implying a distribution', async () => {
    introspect = async () => payload({
      template_card: { template: 't', runs: 1, cost_p50: 0.01, cost_p95: 0.01, priced: true, duration_p50: 5, duration_p95: 5, failure_rate: 0, warnings: [] },
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/p50 and p95 are that one run/i)).toBeTruthy()
  })

  it('answers "what happens next if I say nothing" in words', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText('this run is complete')).toBeTruthy()
  })
})

describe('an empty answer is still an answer', () => {
  it('states the healthy case in words instead of collapsing', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText('Nothing is blocked')).toBeTruthy()
    expect(screen.getByText('Nothing is waiting on you')).toBeTruthy()
  })

  const one = {
    running: { status: 'running', workflow: 'weekly-report', nodes: [{ node_id: 'draft' }] },
    changed: [], blocked: [{ node_id: 'wait' }], approval: [], failed: [{ node_id: 'publish' }],
  }
  const many = {
    running: { status: 'running', workflow: 'weekly-report', nodes: [{ node_id: 'a' }, { node_id: 'b' }] },
    changed: [], blocked: [{ node_id: 'w1' }, { node_id: 'w2' }], approval: [],
    failed: [{ node_id: 'p1' }, { node_id: 'p2' }, { node_id: 'p3' }],
  }

  it('the answer sentences read SINGULAR at one', async () => {
    const base = payload()
    introspect = async () => ({
      ...base,
      timeline: base.timeline.slice(0, 1),
      answers: { ...base.answers, ...one },
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/1 node active/)).toBeTruthy()
    expect(screen.getByText(/1 journal event — see Timeline/)).toBeTruthy()
    expect(screen.getByText(/1 node waiting on something external/)).toBeTruthy()
    expect(screen.getByText(/^1 node failed$/)).toBeTruthy()
  })

  it('and PLURAL above one, with no hedge left anywhere on the panel', async () => {
    const base = payload()
    introspect = async () => ({ ...base, answers: { ...base.answers, ...many } })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/2 nodes active/)).toBeTruthy()
    expect(screen.getByText(/2 journal events — see Timeline/)).toBeTruthy()
    expect(screen.getByText(/2 nodes waiting on something external/)).toBeTruthy()
    expect(screen.getByText(/^3 nodes failed$/)).toBeTruthy()
    const own = [...document.querySelectorAll('[data-type], p, span')]
      .map((e) => e.textContent || '')
      .filter((t) => /node|journal event|gate/.test(t))
      .join(' | ')
    expect(own, 'the panel composes no hedged noun of its own').not.toMatch(/\((s|es)\)/)
  })

  it('each answer takes ITS OWN count, not a neighbour of the same grammatical number', async () => {
    const base = payload()
    introspect = async () => ({
      ...base,
      timeline: base.timeline,
      answers: {
        ...base.answers,
        running: { status: 'running', workflow: 'weekly-report', nodes: [{ node_id: 'draft' }] },
        blocked: [{ node_id: 'w1' }, { node_id: 'w2' }, { node_id: 'w3' }],
        failed: [{ node_id: 'publish' }],
        changed: [], approval: [],
      },
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/1 node active/)).toBeTruthy()
    expect(screen.getByText(/2 journal events — see Timeline/)).toBeTruthy()
    expect(screen.getByText(/3 nodes waiting on something external/)).toBeTruthy()
    expect(screen.getByText(/^1 node failed$/)).toBeTruthy()
  })

  it('riskyText always produces a sentence, including with no risk', () => {
    expect(riskyText(0, 0, 0)).toMatch(/nothing flagged/i)
    expect(riskyText(1, 0, 0)).toMatch(/1 node ran degraded/)
    expect(riskyText(2, 0, 0)).toMatch(/2 nodes ran degraded/)
    expect(riskyText(0, 1, 0)).toMatch(/1 gate may not be checking/)
    expect(riskyText(0, 3, 0)).toMatch(/3 gates may not be checking/)
    expect(riskyText(0, 0, 0.75)).toMatch(/75% of completed steps are unverified/)
    for (const [d, f] of [[0, 0], [1, 0], [2, 0], [0, 1], [0, 3], [1, 1], [4, 7]]) {
      expect(riskyText(d, f, 0.5), `riskyText(${d}, ${f})`).not.toMatch(/\((s|es)\)/)
    }
  })
})

describe('the said-no fake-check badge', () => {
  it('renders the badge and the backend warning verbatim', async () => {
    const warning = '`review` passed 40/40 times and has never rejected — a 100% pass rate over this many runs is evidence it is not checking'
    introspect = async () => payload({
      gates: { review: { node_id: 'review', passes: 40, rejects: 0, retries_consumed: 0, total: 40, pass_rate: 1, fake_check_warning: warning } },
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/never said no/i)).toBeTruthy()
    expect(screen.getByText(warning)).toBeTruthy()
  })

  it('shows NO badge for a gate with too small a sample', async () => {
    introspect = async () => payload({
      gates: { review: { node_id: 'review', passes: 2, rejects: 0, retries_consumed: 0, total: 2, pass_rate: 1, fake_check_warning: '' } },
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    await screen.findByText('review')
    expect(screen.queryByText(/never said no/i)).toBeNull()
  })
})

describe('the edge-decision distribution (PP-8)', () => {
  it('renders a branch case distribution with its dead-case and degenerate warnings verbatim', async () => {
    const degenerate =
      '`router` routed to `bug` in all 12 runs that reached it — its one other case is declared but never chosen, so the selector is doing no work'
    introspect = async () =>
      payload({
        edges: {
          branches: {
            router: {
              path: 'router', cases: { bug: 12, feat: 0 }, routed_runs: 12,
              never_taken: ['feat'], degenerate_warning: degenerate,
            },
          },
          judges: {},
        },
      })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText('router')).toBeTruthy()
    expect(screen.getByText(degenerate)).toBeTruthy()
    expect(screen.getByText(/Never taken: feat/)).toBeTruthy()
    expect(screen.getByText(/does no work/i)).toBeTruthy()
  })

  it('renders a degenerate judge verdict distribution', async () => {
    const warning =
      '`grader` returned `pass` on all 12 verdicts — a judge with one outcome over this many calls is not discriminating'
    introspect = async () =>
      payload({
        edges: {
          branches: {},
          judges: { grader: { node_id: 'grader', verdicts: { pass: 12 }, total: 12, degenerate_warning: warning } },
        },
      })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText('grader')).toBeTruthy()
    expect(screen.getByText(warning)).toBeTruthy()
    expect(screen.getByText(/one verdict/i)).toBeTruthy()
  })

  it('states the empty case in words when a template has no edges', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/no branch or judge edges/i)).toBeTruthy()
  })
})

describe('the Proof section states its own caveats', () => {
  it('renders the caveat as prominently as the numbers', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('tab', { name: /proof/i }))
    expect(await screen.findByText(/a claim about the run rather than proof of it/i)).toBeTruthy()
    expect(screen.getByText('1 of 4')).toBeTruthy()
  })

  it('flags a section that is neither evidenced nor caveated', async () => {
    const dishonest = { summary: 'done', verified_steps: 0, total_steps: 0, coverage: 0, evidence_files: [], warnings: [], honest: false }
    introspect = async () => payload({ proof: dishonest, answers: { ...payload().answers, proof: dishonest } })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('tab', { name: /proof/i }))
    expect(await screen.findByText(/proves nothing/i)).toBeTruthy()
  })
})

describe('the timeline is the journal AND the attempt ledger', () => {
  it('renders events oldest-first and marks a retry', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('tab', { name: /timeline/i }))
    const rows = await screen.findAllByRole('listitem')
    expect(rows[0].textContent).toContain('run_started')
    expect(screen.getByText('attempt 2')).toBeTruthy()
  })

  it('rowSummary never renders undefined when a field is absent', () => {
    expect(rowSummary({ kind: 'step_started', ts: '', node_id: '', instance_path: '', state: '', model: '', detail: '' })).toBe('step_started')
    expect(rowSummary({ kind: 'k', ts: '', node_id: '', instance_path: '', state: 'done', model: '', detail: '' })).toBe('done')
  })
})

describe('a named gap is shown, not swallowed', () => {
  it('renders the backend checklist_gaps rather than blank space', async () => {
    introspect = async () => payload({ checklist_gaps: ['next: what will happen next if I say nothing'] })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/1 of 9 questions cannot be answered/i)).toBeTruthy()
    expect(screen.getByText(/next: what will happen next/i)).toBeTruthy()
  })

  it('shows no gap banner when the payload answers all nine', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    await screen.findByText(/what is running now/i)
    expect(screen.queryByText(/cannot be answered/i)).toBeNull()
  })
})

describe('failures are surfaced, not swallowed', () => {
  it('renders a read failure instead of an empty panel', async () => {
    introspect = async () => { throw new Error('journal unreadable') }
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('journal unreadable')).toBeTruthy())
  })
})

describe('the live touched-items feed', () => {
  it('lists what the run published and what was handed in', async () => {
    introspect = async () => payload({
      touched: [
        { kind: 'artifact', ref: 'report', label: 'Weekly report', action: 'version', detail: '18% changed', node_id: 'write', ts: '2026-08-11T02:00:00+00:00' },
        { kind: 'file', ref: 'input.csv', label: 'input.csv', action: 'dropped', detail: 'text/csv', node_id: '', ts: '2026-08-11T01:00:00+00:00' },
      ],
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('tab', { name: /timeline/i }))
    expect(await screen.findByText('Weekly report')).toBeTruthy()
    expect(screen.getByText('input.csv')).toBeTruthy()
    expect(screen.getByText('version')).toBeTruthy()
  })

  it('renders no Touched section when the run touched nothing', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('tab', { name: /timeline/i }))
    expect(screen.queryByText('Touched')).toBeNull()
  })
})


describe('the run cost line', () => {
  it('marks a derived cost as an estimate and never renders it as exact', () => {
    const text = runCostText(0.1234, true)
    expect(text).toContain('~$0.1234')
    expect(text).toContain('estimated from model prices')
    expect(text).toContain('not a provider-reported charge')
    expect(text.startsWith('$')).toBe(false)
  })

  it('rounds to cents once there is a dollar, matching the Usage panel', () => {
    expect(runCostText(4.2, true)).toContain('~$4.20')
    expect(runCostText(0.0012, true)).toContain('~$0.0012')
  })

  it('does not claim $0.00 when nothing was recorded', () => {
    for (const zero of [0, -0, Number.NaN]) {
      const text = runCostText(zero, false)
      expect(text).not.toContain('$')
      expect(text).toMatch(/[Nn]ot recorded/)
    }
  })

  it('says a measured zero WAS measured, so a free local run is not called unrecorded', () => {
    const text = runCostText(0, true)
    expect(text).not.toContain('$0.00')
    expect(text).toMatch(/measured/)
    expect(text).not.toMatch(/[Nn]ot recorded/)
  })

  it('reaches the DOM as the answer to "what is costing money"', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/~\$.* this run/)).toBeTruthy()
  })

  it('renders an UNPRICED run as not-recorded in the cell, never as ~$0.0000', async () => {
    const base = payload()
    introspect = async () => ({
      ...base,
      stats: { ...base.stats, cost_usd: 0, priced: false },
      answers: { ...base.answers, cost: { ...base.stats, cost_usd: 0, priced: false } },
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText('not recorded')).toBeTruthy()
    expect(screen.queryByText('~$0.0000')).toBeNull()
  })

  it('marks the template percentiles as floors when the sample was not fully priced', async () => {
    const base = payload()
    introspect = async () => ({
      ...base,
      template_card: { ...base.template_card, cost_p50: 0.03, cost_p95: 0.09, priced: false },
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText('≥$0.0300')).toBeTruthy()
    expect(screen.getByText('≥$0.0900')).toBeTruthy()
  })
})
