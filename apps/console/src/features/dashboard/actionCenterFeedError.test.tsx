import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const boom = () => Promise.reject(new Error('gateway down'))

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      approvals: () => Promise.resolve([]),
      inboxPending: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ runs: [], did_ids: [], suppressed: 0 }),
      status: () => Promise.resolve({ update_available: false }),
      notifications: () => Promise.resolve({ notifications: [] }),
      system: () => Promise.resolve({ platform: 'darwin' }),
      discover: () => Promise.resolve({ enabled: true, areas: [] }),
      doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
      ...over,
    },
  }))
}

async function mount() {
  const { DashboardLiveProvider } = await import('./DashboardLive')
  const { ActionCenter } = await import('./widgets/ActionCenter')
  const route = { navigate: vi.fn(), sub: '', navEpoch: 0, query: {}, setQuery: () => {} }
  render(
    <DashboardLiveProvider>
      <ActionCenter {...route} />
    </DashboardLiveProvider>,
  )
}

const ALL_CLEAR = /All clear/i

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('ActionCenter tells a failed lane apart from an empty queue', () => {
  it('a failed approvals read surfaces an alert + Retry, never "All clear"', async () => {
    mockApi({ approvals: boom })
    await mount()
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names the lane that failed').toMatch(/pending approvals/i)
    expect(screen.getByRole('button', { name: /Retry/i }), 'and offers recovery').toBeInTheDocument()
    expect(screen.queryByText(ALL_CLEAR), 'a failed lane is not an empty queue').toBeNull()
  })

  it('Retry re-runs just that lane and clears the failure', async () => {
    let ok = false
    const appr = { id: 'a1', tool: 'shell', tool_purpose: 'run ls', source: '', session: 's1' }
    mockApi({ approvals: () => (ok ? Promise.resolve([appr]) : Promise.reject(new Error('down'))) })
    await mount()
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    ok = true
    await userEvent.click(screen.getByRole('button', { name: /Retry/i }))
    await waitFor(() => expect(screen.getByText('Run shell')).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'a successful retry clears the failure').toBeNull()
  })

  it('a failed lane does not bury the lanes that loaded', async () => {
    const item = { id: 'i1', sender_name: 'Alice from Ops', message: 'ping' }
    mockApi({ approvals: boom, inboxPending: () => Promise.resolve([item]) })
    await mount()
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent).toMatch(/pending approvals/i)
    expect(screen.getByRole('button', { name: /^Reply:/ }), 'the loaded inbox row survives').toBeInTheDocument()
    expect(screen.queryByText(ALL_CLEAR)).toBeNull()
  })

  it('a genuinely empty queue still says "All clear", with no alert', async () => {
    mockApi({})
    await mount()
    await waitFor(() => expect(screen.getByText(ALL_CLEAR)).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'an empty queue is not a failure').toBeNull()
    expect(screen.queryByRole('button', { name: /Retry/i })).toBeNull()
  })
})

describe('the lane failures reach the queue instead of being swallowed', () => {
  it('DashboardLive publishes each lane error + retry, and the silent catch is gone', () => {
    const src = readFileSync(join(process.cwd(), "src/features/dashboard/DashboardLive.tsx"), 'utf8')
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    for (const err of ['approvalsErr', 'inboxErr', 'proposalsErr']) {
      expect(code, `the context carries ${err}`).toMatch(new RegExp(`${err}[,:]`))
    }
    expect(code, 'and the per-lane retries are wired to the loaders')
      .toMatch(/retryApprovals: loadApprovals[\s\S]*retryInbox: loadInbox[\s\S]*retryProposals: loadProposals/)
    for (const call of ['approvals', 'inboxPending', 'skillProposals']) {
      expect(code, `api.${call}() no longer swallows its rejection`)
        .not.toMatch(new RegExp(`api\\.${call}\\(\\)[\\s\\S]{0,90}?catch\\(\\(\\) => \\{\\}\\)`))
    }
  })
})
