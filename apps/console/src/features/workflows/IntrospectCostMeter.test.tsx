import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import type { WorkflowIntrospection } from '../../shared/data/api'
import { CostMeter } from '../../shared/vendor/assistant-ui/elements/cost-meter'
import { IntrospectPanel } from './IntrospectPanel'

let introspect: (id: string) => Promise<WorkflowIntrospection>

vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: { ...actual.api, workflowRunIntrospect: (id: string) => introspect(id) },
  }
})

function recordedRun(priced: boolean): WorkflowIntrospection {
  const stats = {
    run_id: 'r1', tokens: 1200, tokens_recorded: true, cached_tokens: 100,
    cost_usd: priced ? 0.0342 : 0, priced,
    steps_completed: 4, steps_failed: 1, steps_cached: 1, duration_secs: 92.5,
    first_byte_ms: 830, models: ['claude-sonnet'], unverified_steps: 3,
    verification_debt: 0.75, cache_hit_rate: 0.2,
  }
  const proof = {
    summary: '4 steps completed, 1 failed', verified_steps: 1,
    total_steps: 4, coverage: 0.25, evidence_files: [], warnings: [], honest: true,
  }
  return {
    run_id: 'r1', workflow: 'weekly-report', stats,
    gates: {}, edges: { branches: {}, judges: {} },
    template_card: {
      template: 'weekly-report', runs: 12, cost_p50: 0.03, cost_p95: 0.09,
      priced: true, duration_p50: 90, duration_p95: 240, failure_rate: 0.25, warnings: [],
    },
    proof, timeline: [], touched: [], checklist_gaps: [],
    answers: {
      running: { status: 'complete', workflow: 'weekly-report', nodes: [] },
      changed: [], blocked: [], approval: [], failed: [], cost: stats,
      risky: { degraded: [], gates: [], edges: { branches: {}, judges: {} }, verification_debt: 0.75 },
      next: { action: 'nothing', detail: 'this run is complete', queued: [] }, proof,
    },
  }
}

beforeEach(() => { introspect = async () => recordedRun(true) })

describe('workflow run cost uses the recorded pricing boundary', () => {
  it('shows one run cost without claiming a session total or model shares', async () => {
    const { container } = render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    const meter = await screen.findByText('~$0.0342')
    expect(meter.closest('[data-slot="cost-meter"]')).toBeTruthy()
    expect(container.querySelectorAll('[data-slot="cost-meter"]')).toHaveLength(1)
    expect(screen.queryByText('Cost (est.)')).toBeNull()
    expect(screen.queryByText(/session/i)).toBeNull()
    expect(within(meter.closest('[data-slot="cost-meter"]') as HTMLElement).getByRole('status'))
      .toHaveTextContent('Cost breakdown unavailable')
    expect(container.querySelectorAll('[role="meter"]')).toHaveLength(0)
    expect(screen.getByText('Tokens')).toBeInTheDocument()
    expect(screen.getByText('To first output')).toBeInTheDocument()
    expect(screen.getByText('Cost p50')).toBeInTheDocument()
    expect(screen.getByText('Cost p95')).toBeInTheDocument()
    expect(screen.getByText('The nine questions')).toBeInTheDocument()
  })

  it('retains the unpriced cost cell and never presents a CostMeter amount', async () => {
    introspect = async () => recordedRun(false)
    const { container } = render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText('Cost (est.)')).toBeInTheDocument()
    expect(screen.getByText('not recorded')).toBeInTheDocument()
    expect(container.querySelector('[data-slot="cost-meter"]')).toBeNull()
    expect(screen.queryByText('~$0.0000')).toBeNull()
    expect(screen.getByText('Duration')).toBeInTheDocument()
    expect(screen.getByText('Cost p50')).toBeInTheDocument()
  })

  it('preserves the donor session and model breakdown when supplied', () => {
    const { container } = render(<CostMeter
      runCost="$0.0342" sessionCost="$0.0900" lines={[
        { model: 'claude-sonnet', inputTokens: 1200, outputTokens: 500, cost: '$0.0342', share: 1 },
      ]}
    />)
    expect(screen.getByText('$0.0900 session')).toBeInTheDocument()
    expect(screen.getByText('claude-sonnet')).toBeInTheDocument()
    expect(screen.getByText('1.2k in · 0.5k out')).toBeInTheDocument()
    expect(screen.getByRole('meter', { name: 'claude-sonnet cost share' })).toHaveAttribute('aria-valuenow', '100')
    expect(container.querySelector('[role="status"]')).toBeNull()
  })

  it('distinguishes omitted breakdown from a recorded empty list', () => {
    const { container } = render(<CostMeter runCost="$0.0000" lines={[]} />)
    expect(screen.queryByText('Cost breakdown unavailable')).toBeNull()
    expect(screen.queryByText(/session/i)).toBeNull()
    expect(container.querySelectorAll('[role="meter"]')).toHaveLength(0)
  })
})
