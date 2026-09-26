import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { McpServer, ScheduleJob, ScheduleRun, SpawnedAgent, UsageAgg } from '../../../data/api'
import { BackgroundAgentRuns, ConfiguredQuota, McpConfigDialog, MCP_CONFIG_PROVENANCE, McpServerResult, RecordedCheckpoints, RecordedCost, RecordedTrace, ScheduledAgentRun } from '../../../../features/agents/auiAgentPanel'
import { ScheduleCard } from '../../../vendor/assistant-ui/elements/schedule-card'
import { QuotaBanner } from '../../../vendor/assistant-ui/elements/quota-banner'

describe('part 03: background runs and checkpoints', () => {
  const agents: SpawnedAgent[] = [
    { id: 'run-active', task: 'Read logs', done: false, agent: 'Scout', elapsed: 12 },
    { id: 'run-ready', task: 'Write report', done: true, agent: 'Writer', result: 'Report saved' },
    { id: 'run-failed', task: 'Run checks', done: true, agent: 'Checker', error: 'Runner stopped' },
  ]

  it('maps real run IDs and outcomes without a collection control', () => {
    const { container } = render(<BackgroundAgentRuns agents={agents} />)
    expect(container.querySelector('[data-slot="background-inbox"]')).toBeInTheDocument()
    expect(screen.getByText('Scout')).toBeInTheDocument()
    expect(screen.getByText('Writer')).toBeInTheDocument()
    expect(screen.getByText('Checker')).toBeInTheDocument()
    expect(screen.getByText('Runner stopped')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Writer/ })).toBeNull()
  })

  it('passes the selected run ID to an actual navigation callback', () => {
    let opened = ''
    render(<BackgroundAgentRuns agents={agents} onOpen={id => { opened = id }} />)
    fireEvent.click(screen.getByRole('button', { name: /Writer/ }))
    expect(opened).toBe('run-ready')
  })

  it('renders only supplied checkpoint records and no restore control', () => {
    const { container } = render(<RecordedCheckpoints currentId="cp-2" checkpoints={[
      { id: 'cp-1', label: 'Initial', at: '2026-09-25', files: 2 },
      { id: 'cp-2', label: 'Reviewed', at: '2026-09-26', files: 3 },
    ]} />)
    expect(container.querySelector('[data-slot="checkpoint-history"]')).toBeInTheDocument()
    expect(screen.getByText('Initial')).toBeInTheDocument()
    expect(screen.getByText('Reviewed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /restore/i })).toBeNull()
  })

  it('explains missing checkpoint source instead of inventing history', () => {
    render(<RecordedCheckpoints currentId="" checkpoints={null} />)
    expect(screen.getByRole('status')).toHaveTextContent('Checkpoint records unavailable')
  })
})

describe('part 03: schedules and MCP permissions', () => {
  const job: ScheduleJob = { id: 'daily', name: 'Daily review', message: 'Review', enabled: true,
    schedule: '0 9 * * *', next_run_ts: 1790413200 }
  const history: ScheduleRun[] = [
    { run_id: 'run-good', status: 'success', started_at: 1790326800 },
    { run_id: 'run-bad', status: 'failed', started_at: 1790240400, error: 'Unavailable' },
    { run_id: 'run-unknown', status: 'queued', started_at: 1790154000 },
  ]

  it('shows only known historical outcomes and the persisted schedule state', () => {
    const { container } = render(<ScheduledAgentRun job={job} history={history} onSaved={() => {}} />)
    expect(container.querySelector('[data-slot="schedule-card"]')).toBeInTheDocument()
    expect(screen.getByText('Daily review')).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Pause Daily review' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('list', { name: 'Schedule run errors' })).toHaveTextContent('run-bad: Unavailable')
    expect(container.textContent).not.toContain('run-unknown')
  })

  it('does not expose a toggle for an operator managed schedule', () => {
    render(<ScheduledAgentRun job={{ ...job, read_only: true, next_run_ts: null }} history={[]} onSaved={() => {}} />)
    expect(screen.queryByRole('switch')).toBeNull()
    expect(screen.getByText('Enabled')).toBeInTheDocument()
    expect(screen.getByText('Next run unavailable')).toBeInTheDocument()
  })

  const server: McpServer = { name: 'Calendar', status: 'ready', tools: ['list_events'], enabled: true }
  it('shows live server metadata without hosted management controls', () => {
    render(<McpServerResult server={server} allowManage={false} onSaved={() => {}} />)
    expect(screen.getByLabelText('MCP server Calendar')).toHaveTextContent('1 tools')
    expect(screen.getByText('Server controls unavailable for this account.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Disable' })).toBeNull()
  })

  it('shows an authorized toggle only with known enabled state', () => {
    const { rerender } = render(<McpServerResult server={server} allowManage onSaved={() => {}} />)
    expect(screen.getByRole('button', { name: 'Disable' })).toBeInTheDocument()
    rerender(<McpServerResult server={{ ...server, enabled: undefined, error: 'Connection failed' }} allowManage onSaved={() => {}} />)
    expect(screen.getByText('Connection failed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Disable' })).toBeNull()
  })

  it('composes a real server list in a dismissible dialog without adding a server', () => {
    let closed = false
    render(<McpConfigDialog servers={[server]} allowManage={false} onSaved={() => {}} onClose={() => { closed = true }} />)
    expect(MCP_CONFIG_PROVENANCE).toContain('Gideon composition')
    expect(screen.getByRole('dialog', { name: 'MCP configuration' })).toHaveTextContent('Calendar')
    expect(screen.queryByRole('button', { name: 'Disable' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(closed).toBe(true)
  })

  it('states when no server records are configured', () => {
    render(<McpConfigDialog servers={[]} allowManage={false} onSaved={() => {}} onClose={() => {}} />)
    expect(screen.getByRole('status')).toHaveTextContent('No MCP servers are configured')
  })
})

describe('part 03: measured observability only', () => {
  const usage: UsageAgg = { input_tokens: 100, output_tokens: 50, cache_read_tokens: 0,
    cache_creation_tokens: 0, cost_usd: 0.012345, turns: 1, priced: true }

  it('renders actual structured spans and their measured durations', () => {
    const { container } = render(<RecordedTrace totalMs={250} spans={[
      { id: 'span-1', name: 'Tool call', depth: 0, startMs: 20, durationMs: 150, status: 'completed' },
      { id: 'span-2', name: 'Verifier', depth: 1, startMs: 170, durationMs: 80, status: 'failed' },
    ]} />)
    expect(container.querySelector('[data-slot="trace-waterfall"]')).toBeInTheDocument()
    expect(screen.getByText('Tool call')).toBeInTheDocument()
    expect(screen.getByText('Verifier')).toBeInTheDocument()
    expect(screen.getByLabelText('failed, starts at 170ms, runs 80ms')).toBeInTheDocument()
  })

  it('refuses to reinterpret a plain trace string as spans', () => {
    render(<RecordedTrace totalMs={null} spans={null} />)
    expect(screen.getByRole('status')).toHaveTextContent('Structured trace spans unavailable')
  })

  it('renders recorded priced cost without adding model shares', () => {
    const { container } = render(<RecordedCost run={usage} session={{ ...usage, cost_usd: 0.02345 }} lines={[]} />)
    expect(container.querySelector('[data-slot="cost-meter"]')).toBeInTheDocument()
    expect(screen.getByText('$0.012345')).toBeInTheDocument()
    expect(screen.getByText('$0.023450 session')).toBeInTheDocument()
    expect(container.querySelectorAll('[role="meter"]')).toHaveLength(0)
  })

  it('leaves unpriced usage explicitly unavailable', () => {
    render(<RecordedCost run={{ ...usage, priced: false }} session={usage} lines={[]} />)
    expect(screen.getByRole('status')).toHaveTextContent('Priced run and session costs unavailable')
  })

  it('labels an owner configured token limit and its usage basis', () => {
    render(<ConfiguredQuota summary={{ plan: { name: 'Work plan', token_limit: 500, cycle_end: '2026-10-01' },
      usage: { tokens: 125, basis: 'local ledger' } }} />)
    expect(screen.getByLabelText('Configured quota Work plan')).toHaveTextContent('375 tokens left')
    expect(screen.getByText('Owner configured limit · local ledger')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('does not manufacture a ceiling for a plan without one', () => {
    render(<ConfiguredQuota summary={{ plan: { name: 'Open plan', token_limit: null, cycle_end: '' },
      usage: { tokens: 125, basis: 'local ledger' } }} />)
    expect(screen.getByRole('status')).toHaveTextContent('No configured token ceiling for Open plan')
  })
})

describe('part 03: donor control guards', () => {
  it('uses the supplied schedule handler and preserves recorded run outcomes', () => {
    let toggled = false
    render(<ScheduleCard name="Daily review" cadence="0 9 * * *" nextRun="tomorrow" enabled
      history={[{ id: 'run-1', at: '09:00', ok: true }, { id: 'run-2', at: '08:00', ok: false }]}
      onToggle={() => { toggled = true }} />)
    expect(screen.getByText('ok')).toBeInTheDocument()
    expect(screen.getByText('failed')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('switch', { name: 'Pause Daily review' }))
    expect(toggled).toBe(true)
  })

  it('shows paused status without an unhandled schedule switch', () => {
    render(<ScheduleCard name="Daily review" cadence="0 9 * * *" nextRun="tomorrow" enabled={false} history={[]} />)
    expect(screen.getByText('Paused')).toBeInTheDocument()
    expect(screen.getByText('paused')).toBeInTheDocument()
    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('shows a reset date only when supplied and no upgrade action without a handler', () => {
    const { rerender } = render(<QuotaBanner used={75} limit={100} unit="tokens" upgradeLabel="" />)
    expect(screen.getByText('25 tokens left')).toBeInTheDocument()
    expect(screen.queryByText(/resets/)).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
    rerender(<QuotaBanner used={75} limit={100} unit="tokens" resetsIn="on 2026-10-01" upgradeLabel="" />)
    expect(screen.getByText('resets on 2026-10-01')).toBeInTheDocument()
  })

  it('shows an upgrade control only when a handler is present', () => {
    let requested = false
    render(<QuotaBanner used={95} limit={100} unit="tokens" resetsIn="on 2026-10-01"
      upgradeLabel="Review plan" onUpgrade={() => { requested = true }} />)
    expect(screen.getByText('5 tokens left')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Review plan' }))
    expect(requested).toBe(true)
  })
})
