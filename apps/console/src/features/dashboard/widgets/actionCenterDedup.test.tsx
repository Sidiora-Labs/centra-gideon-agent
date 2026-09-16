import { describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'


const PROPOSALS = [
  { id: 'prop-1', slug: 'summarize-logs', description: 'Summarize long logs' },
  { id: 'prop-2', slug: 'triage-errors', description: 'Triage error clusters' },
]
const INBOX = [
  { id: 'inb-m1', channel: 'skills', channel_name: 'skills', message: 'proposal mirror 1',
    sender_id: 's', sender_name: 'skills', classification: 'fyi', confidence: 'high',
    status: 'pending', item_kind: 'proposal', can_reply: false, refs: { skill_proposal: 'prop-1' } },
  { id: 'inb-m2', channel: 'skills', channel_name: 'skills', message: 'proposal mirror 2',
    sender_id: 's', sender_name: 'skills', classification: 'fyi', confidence: 'high',
    status: 'pending', item_kind: 'proposal', can_reply: false, refs: { skill_proposal: 'prop-2' } },
  { id: 'inb-orphan', channel: 'skills', channel_name: 'skills', message: 'orphan mirror',
    sender_id: 's', sender_name: 'skills', classification: 'fyi', confidence: 'high',
    status: 'pending', item_kind: 'proposal', can_reply: false, refs: { skill_proposal: 'prop-gone' } },
  { id: 'inb-real', channel: 'slack', channel_name: 'general', message: 'hey, can you check the deploy?',
    sender_id: 'u1', sender_name: 'Jordan', classification: 'needs_reply', confidence: 'high',
    status: 'pending', item_kind: 'message', can_reply: true, refs: {} },
]

function mockApi() {
  vi.resetModules()
  vi.doMock('../../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      status: () => Promise.resolve({ update_available: false }),
      system: () => Promise.resolve({ platform: 'darwin' }),
      doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
      notifications: () => Promise.resolve({ notifications: [] }),
      discover: () => Promise.resolve({ items: [] }),
      approvals: () => Promise.resolve([]),
      inboxPending: () => Promise.resolve(INBOX),
      skillProposals: () => Promise.resolve({ proposals: PROPOSALS, lastReview: null }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ runs: [], did_ids: [], suppressed: 0 }),
    },
  }))
}

async function mount(Widget: 'ActionCenter' | 'HeroPulse') {
  const { DashboardLiveProvider } = await import('../DashboardLive')
  const mod = Widget === 'ActionCenter' ? await import('./ActionCenter') : await import('./HeroPulse')
  const C = (mod as Record<string, any>)[Widget]
  await act(async () => {
    render(
      <DashboardLiveProvider>
        <C navigate={() => {}} />
      </DashboardLiveProvider>,
    )
    await new Promise((res) => setTimeout(res, 0))
  })
}

describe('Action Center dedups proposal mirrors (#816)', () => {
  it('a mirrored proposal renders once, as the actionable proposal row', async () => {
    mockApi()
    await mount('ActionCenter')
    expect(screen.getByText('Skill: summarize-logs')).toBeTruthy()
    expect(screen.getByText('Skill: triage-errors')).toBeTruthy()
    expect(screen.queryByText('proposal mirror 1')).toBeNull()
    expect(screen.queryByText('proposal mirror 2')).toBeNull()
  })

  it('an orphaned mirror stays visible, and the real message is untouched', async () => {
    mockApi()
    await mount('ActionCenter')
    expect(screen.getByText('orphan mirror')).toBeTruthy()
    expect(screen.getByText(/can you check the deploy/)).toBeTruthy()
  })
})

describe('HeroPulse inbox pill counts messages, not proposal mirrors (#816)', () => {
  it('badges 1 for one real message among three mirrors', async () => {
    mockApi()
    await mount('HeroPulse')
    const pill = screen.getByText('inbox').closest('button')
    expect(pill?.textContent).toContain('1')
    expect(pill?.textContent).not.toContain('4')
  })
})
