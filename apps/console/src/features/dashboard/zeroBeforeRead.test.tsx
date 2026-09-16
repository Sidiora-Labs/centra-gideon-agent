import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const pending = () => new Promise<never>(() => {})
const boom = () => Promise.reject(new Error('gateway down'))

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      approvals: () => Promise.resolve([]),
      inboxPending: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve({ proposals: [] }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      notifications: () => Promise.resolve({ notifications: [] }),
      triggersHistory: () => Promise.resolve({ runs: [], did_ids: [] }),
      status: () => Promise.resolve({}),
      system: () => Promise.resolve({}),
      discover: () => Promise.resolve({ tips: [] }),
      dismissDiscoverTip: () => Promise.resolve({}),
      doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
      updateTask: () => Promise.resolve({}),
      ...over,
    },
  }))
}

const route = { sub: '', navigate: () => {}, navEpoch: 0, setQuery: () => {}, query: {} }

const ALL_PENDING = {
  approvals: pending, inboxPending: pending, skillProposals: pending, uLoops: pending,
  readyTasks: pending, notifications: pending, triggersHistory: pending,
}

async function mount(widget: 'hero' | 'action' | 'tasks' | 'active' | 'schedule') {
  const { DashboardLiveProvider } = await import('./DashboardLive')
  const W = widget === 'hero' ? (await import('./widgets/HeroPulse')).HeroPulse
    : widget === 'action' ? (await import('./widgets/ActionCenter')).ActionCenter
      : widget === 'tasks' ? (await import('./widgets/TasksWidget')).TasksWidget
        : widget === 'active' ? (await import('./widgets/ActiveWork')).ActiveWork
          : (await import('./widgets/ScheduleWidget')).ScheduleWidget
  render(<DashboardLiveProvider><W {...route} /></DashboardLiveProvider>)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the hero states no count before the first read', () => {
  it('renders dashes on the first frame, not five zeros', async () => {
    mockApi(ALL_PENDING)
    await mount('hero')
    expect(screen.queryByLabelText('0 loops running'), 'the zero was a claim from no data').toBeNull()
    expect(screen.queryByLabelText('0 unread')).toBeNull()
    expect(screen.queryByLabelText('0 inbox')).toBeNull()
    expect(screen.getAllByText('—').length, 'all five lanes are unknown').toBe(5)
  })

  it('🪤 says LOADING, not "couldn’t be read" — nothing has failed yet', async () => {
    mockApi(ALL_PENDING)
    await mount('hero')
    expect(screen.getByLabelText('Loading loops running…')).toBeInTheDocument()
    expect(screen.getByLabelText('Loading unread…')).toBeInTheDocument()
    expect(screen.queryByLabelText(/couldn’t be read/), 'no fault has been measured').toBeNull()
  })

  it('a GENUINELY empty lane still reads 0 once read — or this rail says nothing', async () => {
    mockApi({})
    await mount('hero')
    await waitFor(() => expect(screen.getByLabelText('0 loops running')).toBeInTheDocument())
    expect(screen.queryByText('—'), 'nothing is unknown once every read succeeded').toBeNull()
  })

  it('a FAILED lane still says "couldn’t be read" — the sibling rail keeps working', async () => {
    mockApi({ uLoops: boom })
    await mount('hero')
    const pill = await waitFor(() => screen.getByLabelText(/loops running — couldn’t be read/))
    expect(pill.textContent).toContain('—')
    expect(screen.getByLabelText('0 unread')).toBeInTheDocument()
  })
})

describe('a widget does not deliver its verdict before the read', () => {
  it('ActionCenter withholds "All clear" while any lane is unread', async () => {
    mockApi(ALL_PENDING)
    await mount('action')
    expect(screen.queryByText(/All clear/), 'the one sentence that must not appear over an unread approvals lane').toBeNull()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('…and gives it once every lane really is read and empty', async () => {
    mockApi({})
    await mount('action')
    await waitFor(() => expect(screen.getByText(/All clear/)).toBeInTheDocument())
  })

  it('🔑 a FAILED lane shows its retry, NOT an endless skeleton', async () => {
    mockApi({ approvals: boom, inboxPending: boom, skillProposals: boom })
    await mount('action')
    await waitFor(() => expect(screen.getAllByText(/Retry/i).length).toBeGreaterThan(0))
    expect(screen.queryByText(/All clear/)).toBeNull()
  })

  it('TasksWidget does not pitch "New task" to someone whose tasks are in flight', async () => {
    mockApi(ALL_PENDING)
    await mount('tasks')
    expect(screen.queryByText('New task')).toBeNull()
    expect(screen.queryByText(/No tasks ready to work/)).toBeNull()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('…and does pitch it once the lane is read and empty', async () => {
    mockApi({})
    await mount('tasks')
    await waitFor(() => expect(screen.getByText('New task')).toBeInTheDocument())
  })

  it('ActiveWork withholds "No active work"', async () => {
    mockApi(ALL_PENDING)
    await mount('active')
    expect(screen.queryByText(/No active work/)).toBeNull()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('ScheduleWidget withholds its "New trigger" on-ramp', async () => {
    mockApi(ALL_PENDING)
    await mount('schedule')
    expect(screen.queryByText('New trigger')).toBeNull()
    expect(screen.queryByText(/No recent scheduled runs/)).toBeNull()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('…and shows it once the history is read and empty', async () => {
    mockApi({})
    await mount('schedule')
    await waitFor(() => expect(screen.getByText('New trigger')).toBeInTheDocument())
  })

  it('the loading state is ANNOUNCED, not just drawn', async () => {
    mockApi(ALL_PENDING)
    await mount('tasks')
    expect(screen.getByRole('status').textContent, 'the region must have words').toMatch(/Loading tasks…/)
  })
})

describe('the signal itself', () => {
  const code = readFileSync(join(process.cwd(), "src/features/dashboard/DashboardLive.tsx"), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('every counted loader marks its slice in `.finally`, so a REJECTION also counts as read', () => {
    for (const [call, slice] of [
      ['approvals', 'approvals'], ['inboxPending', 'inbox'], ['skillProposals', 'proposals'],
      ['uLoops', 'loops'], ['readyTasks', 'tasks'], ['notifications', 'notifications'],
      ['triggersHistory', 'schedule'],
    ]) {
      expect(code, `${call} must mark '${slice}' read in a finally`)
        .toMatch(new RegExp(`${call}\\([\\s\\S]{0,400}?\\.finally\\(\\(\\) => markRead\\('${slice}'\\)\\)`))
    }
  })

  it('🪤 marking an already-read slice returns the SAME object', () => {
    expect(code).toMatch(/setRead\(\(r\) => \(r\[s\] \? r : \{ \.\.\.r, \[s\]: true \}\)\)/)
  })

  it('VACUITY: the context really publishes `read`, and every slice a consumer names is in it', () => {
    expect(code, '`read` is on the context type').toMatch(/read: Readonly<Record<ReadSlice, boolean>>/)
    expect(code, 'and in the provided value').toMatch(/notificationsErr, read,/)
    for (const s of ['approvals', 'inbox', 'proposals', 'loops', 'tasks', 'notifications', 'schedule']) {
      expect(code, `'${s}' is a declared ReadSlice`).toMatch(new RegExp(`'${s}'`))
    }
  })
})
