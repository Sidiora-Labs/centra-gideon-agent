import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { OwnerQuestionCard } from './OwnerQuestionCard'
import { decodeOwnerQuestion, hydrateTurns, type QuestionSegment } from './chatTypes'

type Fixture = { url: string; token: string; question: QuestionSegment }
let process: ChildProcess | null = null
let fixture: Fixture
const originalFetch = globalThis.fetch
let posted: unknown[] = []

async function start(window = 600) {
  const root = resolve(processCwd(), '../..')
  process = spawn(globalThis.process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', 'checks/runtime/owner_question_ui_fixture.py'], {
    cwd: root, env: { ...globalThis.process.env, PYTHONPATH: 'runtime', GIDEON_HOME: mkdtempSync(resolve(tmpdir(), 'gideon-question-ui-')), QUESTION_WINDOW: String(window) }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  fixture = await new Promise<Fixture>((done, fail) => {
    let output = '', errors = ''
    const timer = setTimeout(() => fail(new Error(`Fixture did not become ready: ${errors}`)), 25000)
    process!.stderr!.on('data', (chunk) => { errors += String(chunk) })
    process!.stdout!.on('data', (chunk) => {
      output += String(chunk)
      const match = /QUESTION_READY:([^\n]+)\n/.exec(output)
      if (match) { clearTimeout(timer); done(JSON.parse(match[1])) }
    })
    process!.on('exit', (code) => { clearTimeout(timer); fail(new Error(`Fixture exited ${code}: ${errors}`)) })
  })
  vi.stubGlobal('fetch', async (input: string | URL | Request, init?: RequestInit) => {
    const path = String(input)
    if (init?.method === 'POST') posted.push(JSON.parse(String(init.body)))
    return originalFetch(path.startsWith('/') ? fixture.url + path : path, { ...init, headers: { ...init?.headers, Authorization: 'Bearer ' + fixture.token } })
  })
}
function processCwd() { return globalThis.process.cwd() }
async function proof() { return (await originalFetch(fixture.url + '/proof', { headers: { Authorization: 'Bearer ' + fixture.token } })).json() }
afterEach(() => { cleanup(); process?.kill('SIGTERM'); process = null; vi.unstubAllGlobals(); posted = [] })

describe('actual authenticated owner HTTP → native card → supported SDK continuation', () => {
  it('restores one card, selects single/multiple and Other, and sends original indices to the model', async () => {
    await start()
    const decoded = decodeOwnerQuestion(fixture.question)!
    const refused = await originalFetch(fixture.url + '/api/chat/questions/answer', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + fixture.token },
      body: JSON.stringify({ session: 'question-ui', id: decoded.id, answers: [{ selected: [0], other: 'Not in the original schema' }, { selected: [0], other: '' }] }),
    })
    expect(refused.status).toBe(400)
    const turns = hydrateTurns([{ role: 'tool', content: 'ask_user', meta: { tool_call_id: 'ui-question-call', owner_question: fixture.question } }], true)
    expect(turns.flatMap((turn) => turn.segments).filter((seg) => seg.kind === 'question')).toEqual([decoded])
    render(<OwnerQuestionCard seg={decoded} session="question-ui" />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Skip' })).not.toHaveAttribute('aria-disabled', 'true'))
    expect(screen.queryByLabelText('Plan — Other')).toBeNull()
    const user = userEvent.setup()
    const emptySend = screen.getByRole('button', { name: 'Send answer' })
    expect(emptySend).toBeEnabled()
    expect(emptySend).toHaveAttribute('aria-disabled', 'true')
    emptySend.focus()
    expect(emptySend).toHaveFocus()
    await user.click(emptySend)
    expect(posted).toEqual([])
    await user.click(screen.getByRole('radio', { name: /Blue/ }))
    await user.click(screen.getByRole('checkbox', { name: /Compile/ }))
    await user.click(screen.getByRole('checkbox', { name: /Runtime/ }))
    await user.type(screen.getByLabelText('Checks — Other'), 'Check the logs')
    await user.click(screen.getByRole('button', { name: 'Send answer' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Answered'))
    expect(posted).toEqual([{ session: 'question-ui', id: decoded.id, answers: [{ selected: [0], other: '' }, { selected: [0, 1], other: 'Check the logs' }], skip: false }])
    await waitFor(async () => expect((await proof()).requests).toHaveLength(2))
    const result = await proof()
    expect(JSON.stringify(result.requests[1].messages)).toContain('Check the logs')
    expect(JSON.stringify(result.requests[1].messages)).toContain('Blue')
    expect(result.messages.some((row: {meta?: {owner_question?: {outcome: string}}}) => row.meta?.owner_question?.outcome === 'answered')).toBe(true)
  }, 30000)
  it('Skip resolves the actual model wait and offers no stale submission', async () => {
    await start()
    render(<OwnerQuestionCard seg={decodeOwnerQuestion(fixture.question)!} session="question-ui" />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Skip' })).not.toHaveAttribute('aria-disabled', 'true'))
    await userEvent.click(screen.getByRole('button', { name: 'Skip' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Skipped'))
    expect(screen.queryByRole('button', { name: 'Send answer' })).toBeNull()
    expect(posted).toHaveLength(1)
    await waitFor(async () => expect(JSON.stringify((await proof()).requests[1].messages)).toContain('skipped'))
  }, 30000)
  it('the original deadline expires the card and restores ended records without an answer POST', async () => {
    await start(.8)
    render(<OwnerQuestionCard seg={decodeOwnerQuestion(fixture.question)!} session="question-ui" />)
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Answer window expired'), { timeout: 3000 })
    expect(screen.queryByRole('button', { name: 'Send answer' })).toBeNull()
    expect(posted).toEqual([])
    cleanup()
    const ended = { ...fixture.question, outcome: 'cancelled' as const, answerable: false, reason: 'The requesting process ended.' }
    render(<OwnerQuestionCard seg={decodeOwnerQuestion(ended)!} session="question-ui" />)
    expect(screen.getByRole('status')).toHaveTextContent('Question cancelled')
    expect(screen.queryByRole('button', { name: 'Skip' })).toBeNull()
  }, 30000)
})
