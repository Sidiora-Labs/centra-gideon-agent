import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../shared/data/data'
import type { InboxItem, PendingApproval } from '../../shared/data/api'


const inboxPending = vi.fn()
const approvals = vi.fn()
const chatSessions = vi.fn()
const resolveApproval = vi.fn()
const resumeWorkflowRun = vi.fn()

vi.mock('../../shared/data/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    inboxPending: (...a: unknown[]) => inboxPending(...a),
    approvals: (...a: unknown[]) => approvals(...a),
    chatSessions: (...a: unknown[]) => chatSessions(...a),
    resolveApproval: (...a: unknown[]) => resolveApproval(...a),
    resumeWorkflowRun: (...a: unknown[]) => resumeWorkflowRun(...a),
  },
}))

const toLanes = vi.fn()
const laneFor = vi.fn()

vi.mock('../../shared/data/attentionLanes', () => ({
  LANES: ['needs-approval', 'your-turn', 'working', 'idle'] as const,
  toLanes: (...a: unknown[]) => toLanes(...a),
  laneFor: (...a: unknown[]) => laneFor(...a),
}))

const LANES_FIXTURE = ['needs-approval', 'your-turn', 'working', 'idle'] as const

import { MissionControl, LANE_REFS, MISSION_CONTROL_VIEW_ID, questionOf } from './MissionControl'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const approval = (over: Partial<PendingApproval> = {}): PendingApproval => ({
  id: 'appr-1', source: 'chat', tool: 'shell.run', session: 'nightly-sweep', ts: 1, ...over,
})

const session = (over: Record<string, unknown> = {}) => ({
  key: 'chat-1', title: 'nightly sweep', messages: 4,
  running: true, stopping: false, pending_approval: false, ...over,
})

const questionItem = (choices: string[] = ['Ship it', 'Hold']): InboxItem => ({
  id: 'inbox-1', channel: 'native', channel_name: 'loop-worker',
  message: 'Which branch should I push?', sender_id: 'agent', sender_name: 'agent',
  classification: 'needs_reply', confidence: 'needs_review', status: 'pending',
  item_kind: 'needs_input',
  refs: {
    workflow: 'run-77',
    workflow_node: 'gate.push',
    resume_token: 'tok-9',
    needs_input: {
      run_id: 'run-77', node_id: 'gate.push', block_kind: 'needs_input',
      blocker: 'Which branch should I push?', choices, resume_token: 'tok-9', actionable: true,
    },
  },
})

const lanes = (over: Partial<Record<string, unknown[]>> = {}) => ({
  'needs-approval': [], 'your-turn': [], working: [], idle: [], ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  inboxPending.mockResolvedValue([])
  approvals.mockResolvedValue([])
  chatSessions.mockResolvedValue([])
  toLanes.mockReturnValue(lanes())
})

describe('the four lanes', () => {
  it('renders all four headings even when every lane is empty', async () => {
    render(<MissionControl />)
    await waitFor(() => expect(toLanes).toHaveBeenCalled())

    for (const name of ['Needs approval', 'Your turn', 'Working', 'Idle']) {
      expect(screen.getByRole('heading', { name })).toBeTruthy()
    }
  })

  it('an EMPTY lane says it is empty rather than vanishing', async () => {
    approvals.mockResolvedValue([approval()])
    toLanes.mockReturnValue(lanes({ 'needs-approval': [{ id: 'c1', title: 'shell.run', approval: approval() }] }))
    render(<MissionControl />)

    expect(await screen.findByText('Nothing is waiting on an answer from you.')).toBeTruthy()
    expect(screen.getByText('Nothing is running right now.')).toBeTruthy()
    expect(screen.getByText('Nothing is idle.')).toBeTruthy()
    expect(screen.queryByText('Nothing is waiting on your approval.')).toBeNull()
  })
})

describe('approving from a lane', () => {
  const card = { id: 'c1', title: 'shell.run', detail: 'rm -rf ./build', approval: approval() }

  beforeEach(() => {
    approvals.mockResolvedValue([approval()])
    toLanes.mockReturnValue(lanes({ 'needs-approval': [card] }))
  })

  it('names WHICH item the approve button acts on', async () => {
    render(<MissionControl />)
    const btn = await screen.findByRole('button', { name: /^Approve .*shell\.run/ })
    expect(btn).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Reject .*shell\.run/ })).toBeTruthy()
  })

  it('POSTs the approval id and the approve action, then shows the card resolved', async () => {
    resolveApproval.mockResolvedValue({ ok: true })
    render(<MissionControl />)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))

    expect(resolveApproval).toHaveBeenCalledWith('appr-1', 'approve')
    expect(await screen.findByRole('status')).toHaveTextContent('Approved.')
    await waitFor(() => expect(screen.queryByRole('button', { name: /^Approve/ })).toBeNull())
  })

  it('rejecting posts the reject action', async () => {
    resolveApproval.mockResolvedValue({ ok: true })
    render(<MissionControl />)
    await userEvent.click(await screen.findByRole('button', { name: /^Reject/ }))
    expect(resolveApproval).toHaveBeenCalledWith('appr-1', 'reject')
  })

  it('a FAILED approve says so on the card, and the card does NOT read as resolved', async () => {
    resolveApproval.mockRejectedValue(new Error('approval appr-1 has expired'))
    render(<MissionControl />)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('approval appr-1 has expired')
    expect(alert).toHaveTextContent(/try again/i)
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.getByRole('button', { name: /^Approve/ })).toBeTruthy()
  })
})

describe('answering a pending question', () => {
  const card = { id: 'q1', title: 'loop-worker', item: questionItem() }

  beforeEach(() => {
    inboxPending.mockResolvedValue([questionItem()])
    toLanes.mockReturnValue(lanes({ 'your-turn': [card] }))
  })

  it('renders the options as buttons, each naming its item', async () => {
    render(<MissionControl />)
    expect(await screen.findByRole('button', { name: /^Answer .*Ship it$/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Answer .*Hold$/ })).toBeTruthy()
    expect(screen.getByText('Which branch should I push?')).toBeTruthy()
  })

  it('answering resumes the run with the CHOSEN option and its resume token', async () => {
    resumeWorkflowRun.mockResolvedValue({ resumed: true })
    render(<MissionControl />)
    await userEvent.click(await screen.findByRole('button', { name: /Hold$/ }))

    expect(resumeWorkflowRun).toHaveBeenCalledWith('run-77', { answer: 'Hold', resume_token: 'tok-9' })
    expect(await screen.findByRole('status')).toHaveTextContent('the run is moving again')
  })

  it('a FAILED answer says so and keeps the options clickable', async () => {
    resumeWorkflowRun.mockRejectedValue(new Error('run-77 is no longer parked'))
    render(<MissionControl />)
    await userEvent.click(await screen.findByRole('button', { name: /Ship it$/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('run-77 is no longer parked')
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.getByRole('button', { name: /Ship it$/ })).toBeTruthy()
  })

  it('a question with NO options says where to answer it instead of faking a text box', async () => {
    const bare = { id: 'q2', title: 'loop-worker', item: questionItem([]) }
    toLanes.mockReturnValue(lanes({ 'your-turn': [bare] }))
    render(<MissionControl />)

    expect(await screen.findByText(/no preset options/)).toBeTruthy()
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('button', { name: /^Answer/ })).toBeNull()

    const link = screen.getByRole('link', { name: /^Open the run:/ })
    expect(link.getAttribute('href')).toMatch(/^#\/workflows\/runs\//)
  })
})

describe('a failed read', () => {
  it('says it could not load rather than painting four empty lanes', async () => {
    approvals.mockRejectedValue(new Error('gateway is restarting'))
    render(<MissionControl />)
    expect(await screen.findByRole('alert')).toHaveTextContent('gateway is restarting')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })
})

describe('the lane split comes from lib/attentionLanes, not from this view', () => {
  it('consults the mocked toLanes with the items, the approvals AND the session activity', async () => {
    const item = questionItem()
    const appr = approval()
    inboxPending.mockResolvedValue([item])
    approvals.mockResolvedValue([appr])
    chatSessions.mockResolvedValue([session()])
    render(<MissionControl />)

    await waitFor(() =>
      expect(toLanes).toHaveBeenCalledWith(
        [item],
        [appr],
        [{ key: 'chat-1', title: 'nightly sweep', running: true, stopping: false, pending_approval: false }],
      ),
    )
  })

  it('hands the three lists over UNMERGED — a mirrored approval is on the wire twice', async () => {
    const appr = approval({ id: 'appr-9' })
    const mirror: InboxItem = {
      ...questionItem(), id: 'inbox-mirror', item_kind: 'agent_request',
      refs: { session: 'chat-1', approval: 'appr-9' },
    }
    inboxPending.mockResolvedValue([mirror])
    approvals.mockResolvedValue([appr])
    render(<MissionControl />)

    await waitFor(() => expect(toLanes).toHaveBeenCalled())
    const [gotItems, gotApprovals] = toLanes.mock.calls[toLanes.mock.calls.length - 1]
    expect(gotItems).toEqual([mirror])
    expect(gotApprovals).toEqual([appr])
  })

  it('normalizes a session the list endpoint typed WITHOUT stopping/pending_approval', async () => {
    chatSessions.mockResolvedValue([{ key: 'chat-2', title: 'old chat', messages: 3 }])
    render(<MissionControl />)

    await waitFor(() => expect(toLanes).toHaveBeenCalled())
    const activity = toLanes.mock.calls[toLanes.mock.calls.length - 1][2]
    expect(activity).toEqual([
      { key: 'chat-2', title: 'old chat', running: false, stopping: false, pending_approval: false },
    ])
  })

  it('feeds the Working lane from the session activity, not from the attention store', async () => {
    chatSessions.mockResolvedValue([session()])
    toLanes.mockReturnValue(lanes({ working: [{ id: 'session:chat-1', title: 'nightly sweep' }] }))
    render(<MissionControl />)

    expect(await screen.findByText('nightly sweep')).toBeTruthy()
    expect(screen.queryByText('Nothing is running right now.')).toBeNull()
  })

  it('renders the lanes the sibling returned, in the sibling’s declared order', async () => {
    toLanes.mockReturnValue(lanes({ working: [{ id: 'w1', title: 'nightly sweep' }] }))
    render(<MissionControl />)
    await screen.findByText('nightly sweep')

    const headings = screen.getAllByRole('heading').map((h) => h.textContent)
    expect(headings).toEqual(['Mission Control', 'Needs approval', 'Your turn', 'Working', 'Idle'])
  })
})

describe('LANE_REFS — the one reconciliation point with views_store', () => {
  it('covers every lane exactly once, in the sibling’s order', () => {
    expect(Object.keys(LANE_REFS)).toEqual([...LANES_FIXTURE])
    expect(new Set(Object.values(LANE_REFS)).size).toBe(LANES_FIXTURE.length)
    for (const ref of Object.values(LANE_REFS)) expect(ref.startsWith('core:')).toBe(true)
  })
})

describe('questionOf — reading the options off the wire', () => {
  it('reads choices and the resume token from refs.needs_input', () => {
    const q = questionOf(questionItem(['a', 'b']))
    expect(q).toMatchObject({ runId: 'run-77', resumeToken: 'tok-9', choices: ['a', 'b'] })
  })

  it('is null for a row that carries no needs_input card', () => {
    expect(questionOf({ ...questionItem(), refs: { workflow: 'run-1' } })).toBeNull()
    expect(questionOf({ ...questionItem(), refs: undefined })).toBeNull()
    expect(questionOf(undefined)).toBeNull()
  })
})

describe('the route is mounted in the shell', () => {
  const app = readFileSync(join(process.cwd(), "src/app/shell/App.tsx"), 'utf8')

  it('parses App.tsx (guards against a vacuous sweep)', () => {
    expect(app.length).toBeGreaterThan(1000)
    expect(app).toContain('function renderPage')
  })

  it('lazy-imports the page', () => {
    expect(app).toMatch(/const MissionControl = lazyRoute\('mission-control', \(\) => import\(/)
  })

  it(`dispatches '${MISSION_CONTROL_VIEW_ID}' to it, not to the coming-soon fallback`, () => {
    expect(app).toMatch(new RegExp(`case '${MISSION_CONTROL_VIEW_ID}': return <MissionControl`))
  })

  it('is in ROUTABLE, so the hash route is not rejected before it renders', () => {
    const routable = app.match(/const ROUTABLE = new Set\(\[(.*?)\]\)/s)
    expect(routable, 'the ROUTABLE literal moved — re-point this rail').toBeTruthy()
    expect(routable![1]).toContain(`'${MISSION_CONTROL_VIEW_ID}'`)
  })

  it('is reachable by a user, via the command palette', () => {
    expect(app).toMatch(/id: 'go:mission-control'/)
    expect(app).toMatch(/label: 'Mission Control'/)
  })
})
