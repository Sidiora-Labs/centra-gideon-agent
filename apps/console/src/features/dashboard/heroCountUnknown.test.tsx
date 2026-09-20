import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const boom = () => Promise.reject(new Error('gateway down'))
const empty = { approvals: [], inbox: [], proposals: [], loops: [], tasks: [], notifications: [] }

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      approvals: () => Promise.resolve([]),
      inboxOpen: () => Promise.resolve([]),
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
      ...over,
    },
  }))
}

async function mountHero() {
  const { DashboardLiveProvider } = await import('./DashboardLive')
  const { HeroPulse } = await import('./widgets/HeroPulse')
  render(
    <DashboardLiveProvider>
      <HeroPulse sub="" navigate={() => {}} navEpoch={0} setQuery={() => {}} query={{}} />
    </DashboardLiveProvider>,
  )
}

describe('the hero strip never states a count it could not read', () => {
  beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

  it('a failed loops read renders a dash, not 0 — and says so to a screen reader', async () => {
    mockApi({ uLoops: boom })
    await mountHero()
    const pill = await waitFor(() => screen.getByLabelText(/loops running — couldn’t be read/))
    expect(pill.textContent, 'the dash stands in for the number').toContain('—')
    expect(pill.getAttribute('aria-label'), 'and no zero is spoken').not.toMatch(/^0 /)
  })

  it('a failed notifications read renders a dash for unread', async () => {
    mockApi({ notifications: boom })
    await mountHero()
    const pill = await waitFor(() => screen.getByLabelText(/unread — couldn’t be read/))
    expect(pill.textContent).toContain('—')
  })

  it('a failed tasks read renders a dash for tasks ready', async () => {
    mockApi({ readyTasks: boom })
    await mountHero()
    const pill = await waitFor(() => screen.getByLabelText(/tasks ready — couldn’t be read/))
    expect(pill.textContent).toContain('—')
  })

  it('a GENUINELY empty lane still reads 0 — the other half, or this rail says nothing', async () => {
    mockApi({})
    await mountHero()
    await waitFor(() => expect(screen.getByLabelText('0 loops running')).toBeInTheDocument())
    expect(screen.getByLabelText('0 unread')).toBeInTheDocument()
    expect(screen.queryByText('—'), 'nothing is unknown when every read succeeded').toBeNull()
    expect(empty.loops, 'the fixture really is empty').toHaveLength(0)
  })
})

describe('DashboardLive exposes the failure of every lane the hero counts', () => {
  const code = readFileSync(join(process.cwd(), "src/features/dashboard/DashboardLive.tsx"), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('the three counted loaders capture their rejection instead of swallowing it', () => {
    for (const [slice, setter] of [['uLoops', 'setLoopsErr'], ['readyTasks', 'setTasksErr'], ['notifications', 'setNotificationsErr']]) {
      expect(code, `${slice}'s rejection must reach ${setter}`).toMatch(new RegExp(`${slice}\\(\\)[\\s\\S]{0,240}?${setter}\\)\\(e\\)`))
    }
  })

  it('and publishes them on the context', () => {
    for (const f of ['loopsErr', 'tasksErr', 'notificationsErr']) {
      expect(code, `${f} is declared on the context type`).toMatch(new RegExp(`${f}: unknown`))
    }
  })
})
