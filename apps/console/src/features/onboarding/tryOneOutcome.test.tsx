import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'


const createKnowledgeItem = vi.fn()
const knowledgeSearchForContext = vi.fn()
const createSchedule = vi.fn()
const runSchedule = vi.fn()
const notifications = vi.fn()
const createULoop = vi.fn()
const uLoopAction = vi.fn()

vi.mock('../../shared/data/api', () => ({
  api: {
    createKnowledgeItem: (...a: unknown[]) => createKnowledgeItem(...a),
    knowledgeSearchForContext: (...a: unknown[]) => knowledgeSearchForContext(...a),
    createSchedule: (...a: unknown[]) => createSchedule(...a),
    runSchedule: (...a: unknown[]) => runSchedule(...a),
    notifications: () => notifications(),
    createULoop: (...a: unknown[]) => createULoop(...a),
    uLoopAction: (...a: unknown[]) => uLoopAction(...a),
  },
}))

import { TryOneStep } from './TryOneStep'
import { KNOWLEDGE_SEED, REMINDER_SEED, LOOP_SEED } from './tryOneFlows'

const onProgress = vi.fn()
const onDone = vi.fn()
const onSkip = vi.fn()
const onExitTo = vi.fn()

const RETRIEVED_ONLY = 'One home directory holds the lot; back that folder up and you have backed up your agent.'

function happy() {
  createKnowledgeItem.mockResolvedValue({ id: 'k-1', title: KNOWLEDGE_SEED.title })
  knowledgeSearchForContext.mockResolvedValue({
    query: KNOWLEDGE_SEED.question,
    results: [{
      id: 'k-1', title: KNOWLEDGE_SEED.title, match_type: 'keyword', tokens: 54,
      content: RETRIEVED_ONLY,
    }],
    total_tokens: 54, max_tokens: 1200,
  })
  createSchedule.mockResolvedValue({
    ok: true,
    trigger: { raw_id: 'clock:daily-check-in', schedule: 'At 09:00 AM', next_run_ts: 1786870800 },
  })
  runSchedule.mockResolvedValue({ ok: true, name: REMINDER_SEED.name, result: 'ran' })
  notifications.mockResolvedValue({
    notifications: [{ kind: 'success', title: REMINDER_SEED.title, body: REMINDER_SEED.body }],
    unread: 0,
  })
  createULoop.mockResolvedValue({ id: 'lp-1', status: 'ready', task: LOOP_SEED.task, max_cycles: 1 })
  uLoopAction.mockResolvedValue({ id: 'lp-1', status: 'running', task: LOOP_SEED.task, max_cycles: 1 })
}

beforeEach(() => {
  vi.clearAllMocks()
  happy()
})

function mount() {
  render(<TryOneStep onProgress={onProgress} onDone={onDone} onSkip={onSkip} onExitTo={onExitTo} />)
}

describe('nothing runs until the user asks for it', () => {
  it('mounting the step calls NO flow endpoint', () => {
    mount()
    for (const fn of [createKnowledgeItem, createSchedule, createULoop, uLoopAction, runSchedule]) {
      expect(fn).not.toHaveBeenCalled()
    }
  })

  it('all three cards are offered, none pre-run', () => {
    mount()
    expect(screen.getByRole('button', { name: /Save and ask/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Create and fire once/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Start it/ })).toBeTruthy()
    expect(screen.queryByText(/Done$/)).toBeNull()
  })
})

describe('knowledge card — ingests for real, then asks and shows the answering passage', () => {
  it('calls ingest then search, with the seeded note and question', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Save and ask/ }))
    await waitFor(() => expect(knowledgeSearchForContext).toHaveBeenCalled())
    expect(createKnowledgeItem).toHaveBeenCalledWith({
      type: 'note', title: KNOWLEDGE_SEED.title, content: KNOWLEDGE_SEED.content, tags: ['first-run'],
    })
    expect(knowledgeSearchForContext).toHaveBeenCalledWith(KNOWLEDGE_SEED.question, 1200)
  })

  it('renders the PASSAGE the retrieval returned, not the note it sent', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Save and ask/ }))
    expect(await screen.findByText(RETRIEVED_ONLY)).toBeTruthy()
    expect(screen.queryByText(new RegExp(KNOWLEDGE_SEED.content.slice(0, 60)))).toBeNull()
    expect(screen.getByText(KNOWLEDGE_SEED.question)).toBeTruthy()
    expect(screen.getByText(/keyword · 54 tokens of context/)).toBeTruthy()
  })

  it('a note that saves but is not retrievable is a FAILURE, not a success', async () => {
    knowledgeSearchForContext.mockResolvedValue({ query: '', results: [], total_tokens: 0, max_tokens: 1200 })
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Save and ask/ }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText(/The knowledge index is not answering/)).toBeTruthy()
    expect(onProgress).not.toHaveBeenCalled()
  })
})

describe('reminder card — creates it, fires it, and reads the notification back', () => {
  it('creates a notify-action schedule and fires that exact trigger', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Create and fire once/ }))
    await waitFor(() => expect(runSchedule).toHaveBeenCalled())
    const [body] = createSchedule.mock.calls[0] as [Record<string, unknown>]
    expect(body.cron).toBe(REMINDER_SEED.cron)
    expect(body.action).toEqual({
      provider: 'notify',
      config: { title_template: REMINDER_SEED.title, body_template: REMINDER_SEED.body, kind: 'success' },
    })
    expect(runSchedule).toHaveBeenCalledWith('clock:daily-check-in')
  })

  it('renders the notification text that actually landed, plus the next fire time', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Create and fire once/ }))
    expect(await screen.findByText(`${REMINDER_SEED.title} — ${REMINDER_SEED.body}`)).toBeTruthy()
    expect(screen.getByText('At 09:00 AM')).toBeTruthy()
    expect(notifications).toHaveBeenCalled()
  })

  it('a 200 with ok:false is a failure — the action did not run', async () => {
    runSchedule.mockResolvedValue({ ok: false, name: REMINDER_SEED.name, result: 'no action resolved' })
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Create and fire once/ }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('no action resolved')).toBeTruthy()
  })

  it('a fire that produces no notification is a failure', async () => {
    notifications.mockResolvedValue({ notifications: [], unread: 0 })
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Create and fire once/ }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText(/no .* notification reached the dashboard/)).toBeTruthy()
  })
})

describe('loop card — creates and STARTS a real loop', () => {
  it('creates a one-cycle general loop and starts it', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Start it/ }))
    await waitFor(() => expect(uLoopAction).toHaveBeenCalled())
    expect(createULoop).toHaveBeenCalledWith({ kind: 'general', task: LOOP_SEED.task, max_cycles: 1 })
    expect(uLoopAction).toHaveBeenCalledWith('lp-1', 'start')
  })

  it('renders the status the START response reported', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Start it/ }))
    expect(await screen.findByText('running')).toBeTruthy()
    expect(screen.getByText(/1 cycle — it stops on its own/)).toBeTruthy()
  })

  it('a loop that is created but never leaves `ready` is a failure', async () => {
    uLoopAction.mockResolvedValue({ id: 'lp-1', status: 'ready', task: LOOP_SEED.task, max_cycles: 1 })
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Start it/ }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText(/did not start — it is "ready"/)).toBeTruthy()
  })
})

describe('first_success is written by the flow, not by the click', () => {
  it('records only its own key, and only after the flow returned', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Save and ask/ }))
    await waitFor(() => expect(onProgress).toHaveBeenCalledWith({ first_success: { knowledge: true } }))
    expect(onProgress).toHaveBeenCalledTimes(1)
  })

  it('each card writes its own flag', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Create and fire once/ }))
    await waitFor(() => expect(onProgress).toHaveBeenCalledWith({ first_success: { trigger: true } }))
    fireEvent.click(screen.getByRole('button', { name: /Start it/ }))
    await waitFor(() => expect(onProgress).toHaveBeenCalledWith({ first_success: { loop: true } }))
  })

  it('a failed flow writes NOTHING', async () => {
    createULoop.mockRejectedValue(new Error('loop store unavailable'))
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Start it/ }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(onProgress).not.toHaveBeenCalled()
  })
})

describe('the step is an offer, never a wall', () => {
  it('Continue works with nothing tried, and reports it honestly', () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
    expect(onDone).toHaveBeenCalledWith('Skipped')
  })

  it('Continue reports how many actually succeeded', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Save and ask/ }))
    await waitFor(() => expect(onProgress).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
    expect(onDone).toHaveBeenCalledWith('1 of 3 tried')
  })

  it('a succeeded card offers a link to what it made — through the flow exit, not a raw hash', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Save and ask/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Open the note/ }))
    expect(onExitTo).toHaveBeenCalledWith('knowledge/item/k-1')
  })
})
