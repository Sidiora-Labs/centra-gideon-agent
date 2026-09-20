import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

const TIP = {
  id: 'chat', area: 'Talk to it', title: 'Start a conversation',
  lesson: 'Chat is the front door.', try_it: { route: 'chat/new', query: {}, label: 'Open Chat' },
}
const FEED = { enabled: true, areas: [{ area: 'Talk to it', tips: [TIP] }] }

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      status: () => Promise.resolve({ update_available: false }),
      system: () => Promise.resolve({ platform: 'darwin' }),
      doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
      notifications: () => Promise.resolve({ notifications: [] }),
      discover: () => Promise.resolve(FEED),
      approvals: () => Promise.resolve([]),
      inboxOpen: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ runs: [], did_ids: [], suppressed: 0 }),
      ...over,
    },
  }))
}

async function mount(navigate = vi.fn()) {
  const { DashboardLiveProvider, useDashboardLive } = await import('./DashboardLive')
  const { Discover } = await import('./widgets/Discover')
  function Probe() {
    const live = useDashboardLive()
    return <span data-testid="probe">{live.discover === null ? 'unread' : 'read'}</span>
  }
  const route = { navigate, sub: '', navEpoch: 0, query: {}, setQuery: () => {} }
  render(
    <DashboardLiveProvider>
      <Discover {...route} />
      <Probe />
    </DashboardLiveProvider>,
  )
  await waitFor(() => expect(screen.getByTestId('probe')).toBeInTheDocument())
  return { navigate }
}

const OFF = /Discover tips are off/i
const FAILED = /Couldn’t load your tips/i

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the dashboard Discover slot distinguishes failed, unread and off', () => {
  it('a FAILED read says it could not read, not that a setting is off', async () => {
    mockApi({ discover: () => Promise.reject(new Error('Failed to fetch')) })
    await mount()
    await waitFor(() => expect(screen.getByText(FAILED)).toBeInTheDocument())
    expect(screen.queryByText(OFF), 'a dead endpoint is not the user’s choice').toBeNull()
  })

  it('an UNREAD slice says nothing at all — it does not pre-announce a setting', async () => {
    mockApi({ discover: () => new Promise(() => {}) })
    await mount()
    expect(screen.getByTestId('probe').textContent).toBe('unread')
    expect(screen.queryByText(OFF), 'not yet read is not "off"').toBeNull()
    expect(screen.queryByText(FAILED), 'and it is not a failure either').toBeNull()
  })

  it('a genuinely OFF feed states the fact AND offers the way to change it', async () => {
    mockApi({ discover: () => Promise.resolve({ enabled: false, areas: [] }) })
    const { navigate } = await mount()
    await waitFor(() => expect(screen.getByText(OFF)).toBeInTheDocument())
    const cta = screen.getByRole('button', { name: /Open Settings/i })
    await userEvent.click(cta)
    expect(navigate).toHaveBeenCalledWith('settings/legibility')
    expect(screen.queryByText(FAILED)).toBeNull()
  })

  it('a live feed still renders its deck, and none of the three sentences appear', async () => {
    mockApi({})
    await mount()
    await waitFor(() => expect(screen.getByText('Start a conversation')).toBeInTheDocument())
    expect(screen.queryByText(OFF)).toBeNull()
    expect(screen.queryByText(FAILED)).toBeNull()
    expect(screen.queryByRole('button', { name: /Open Settings/i })).toBeNull()
  })
})

describe('the split lives in the shared feed, and matches its already-correct sibling', () => {
  it('the live context carries the tips read’s own failure', () => {
    const code = read('features/dashboard/DashboardLive.tsx')
    expect(code, 'declared on the interface').toMatch(/discoverErr: unknown/)
    expect(code, 'and the context carries it').toMatch(/discover, discoverErr,/)
    expect(code, 'no bare swallow may remain on the tips read').not.toMatch(
      /api\.discover\(\)[\s\S]{0,80}catch\(\(\) => \{\}\)/,
    )
  })

  it('the widget and the page reach the SAME setting by the same route', () => {
    const widget = read('features/dashboard/widgets/Discover.tsx')
    const page = read('features/discover/DiscoverPage.tsx')
    for (const [what, src] of [['widget', widget], ['page', page]] as const) {
      expect(src, `${what} must route the off case to the legibility settings`).toMatch(
        /navigate\('settings\/legibility'\)/,
      )
    }
    expect(widget, 'the old conflated gate must be gone').not.toMatch(/!discover \|\| !discover\.enabled/)
  })
})
