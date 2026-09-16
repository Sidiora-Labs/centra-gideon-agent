import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'


const REMEDIATION_ROW = {
  kind: 'schedule',
  id: 'schedule:system:self-remediation',
  raw_id: 'system:self-remediation',
  name: 'Self-remediation',
  enabled: true,
  schedule: 'adaptive — every 60m healthy, 5m degraded (now: healthy)',
  action: { provider: 'self-remediation', config: {} },
  last_run_ts: null,
  last_run_status: '',
  last_status: 'ok',
  run_count: 0,
  next_run_ts: null,
  is_running: false,
  created_by: 'system',
  author: '',
  read_only: false,
  broken: [],
}

const { STATE } = vi.hoisted(() => ({ STATE: { jobs: [] as unknown[] } }))

vi.mock('../../shared/data/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    schedules: () => Promise.resolve({ jobs: STATE.jobs }),
    hooks: () => Promise.resolve([]),
    storeTriggers: () => Promise.resolve([]),
    eventTriggers: () => Promise.resolve([]),
    actionProviders: () => Promise.resolve([]),
    autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
  },
}))

const { TriggersSection } = await import('./TriggersSection')

const mount = (query: Record<string, string> = {}) =>
  render(
    <TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={query} setQuery={() => {}} />,
  )

beforeEach(() => { sessionStorage.clear() })

describe('the self-remediation trigger on the Triggers page', () => {
  beforeEach(() => { STATE.jobs = [REMEDIATION_ROW] })

  it('renders the engine as a row a user can see and open', async () => {
    mount()
    await waitFor(() => expect(screen.getByText('Self-remediation')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Self-remediation' })).toBeInTheDocument()
  })

  it('tells the user BOTH cadences and which one is live', async () => {
    mount()
    await waitFor(() => expect(screen.getByText(REMEDIATION_ROW.schedule)).toBeInTheDocument())
  })

  it('names the action it runs, from the declared label rather than the id-prettifier', async () => {
    mount()
    await waitFor(() => expect(screen.getByText('Self-Remediation')).toBeInTheDocument())
  })

  it('survives the type filter it belongs to, and is excluded by one it does not', async () => {
    mount({ filter: 'schedule' })
    await waitFor(() => expect(screen.getByText('Self-remediation')).toBeInTheDocument())

    mount({ filter: 'lifecycle' })
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'No matching triggers' })).toBeInTheDocument(),
    )
  })
})

describe('the vacuity control', () => {
  beforeEach(() => { STATE.jobs = [] })

  it('does not render the name when the backend serves no such row', async () => {
    mount()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No triggers' })).toBeInTheDocument())
    expect(screen.queryByText('Self-remediation')).not.toBeInTheDocument()
    expect(screen.queryByText(REMEDIATION_ROW.schedule)).not.toBeInTheDocument()
  })
})
