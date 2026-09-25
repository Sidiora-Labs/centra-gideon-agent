import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import Cognition from './Cognition'

let server: ChildProcess
let origin: string
let home: string
const networkFetch = globalThis.fetch
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-cognition-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_cognition import register
async def main():
 app = web.Application()
 register(app, Path(os.environ['GIDEON_HOME']))
 runner = web.AppRunner(app)
 await runner.setup()
 site = web.TCPSite(runner, '127.0.0.1', 0)
 await site.start()
 print(site._server.sockets[0].getsockname()[1], flush=True)
 await asyncio.Event().wait()
asyncio.run(main())
`], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'runtime'), GIDEON_HOME: home } })
  origin = await new Promise<string>((resolveOrigin, reject) => {
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP server exited ${code}`)))
    server.stdout!.once('data', data => resolveOrigin(`http://127.0.0.1:${String(data).trim()}`))
    server.stderr!.on('data', data => { if (String(data).includes('Traceback')) reject(new Error(String(data))) })
  })
  globalThis.fetch = (input, init) => networkFetch(new URL(String(input), origin), init)
})
afterAll(async () => {
  cleanup()
  globalThis.fetch = networkFetch
  if (server?.exitCode === null) await new Promise<void>(resolveExit => { server.once('exit', () => resolveExit()); server.kill('SIGTERM') })
  rmSync(home, { recursive: true, force: true })
})

const base = '/api/capabilities/wellbeing/cognition/sessions'
const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })
const open = () => { window.history.replaceState(null, '', '#/capabilities/wellbeing/cognition?shell=retained'); return render(<Cognition />) }

test('actual arithmetic trials score persisted responses and reopen via shell hash', async () => {
  const view = open()
  await screen.findByText('No exercise sessions.')
  change('Number of trials (1–20)', '2')
  change('Time limit in seconds (1–600)', '60')
  fireEvent.click(screen.getByRole('button', { name: 'Start exercise' }))
  await screen.findByRole('heading', { name: 'Trial 1' })
  const question = screen.getByLabelText('Arithmetic question')
  const left = Number(question.getAttribute('data-left'))
  const right = Number(question.getAttribute('data-right'))
  const expected = question.getAttribute('data-operator') === '+' ? left + right : left - right
  change('Your arithmetic answer', String(expected))
  fireEvent.click(screen.getByRole('button', { name: 'Submit answer' }))
  await screen.findByRole('heading', { name: 'Trial 2' })
  expect(screen.getByText('Answered: 1 · Correct: 1')).toBeInTheDocument()
  change('Your arithmetic answer', 'incorrect value')
  fireEvent.click(screen.getByRole('button', { name: 'Submit answer' }))
  await screen.findByRole('heading', { name: 'Session: completed' })
  expect(screen.getByText('Answered: 2 · Correct: 1')).toBeInTheDocument()
  expect(screen.getByText('Accuracy: 50%')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Submit answer' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Cancel exercise' })).not.toBeInTheDocument()
  const params = new URLSearchParams(location.hash.split('?')[1])
  expect(params.get('shell')).toBe('retained')
  expect(location.hash.split('?')[0]).toBe('#/capabilities/wellbeing/cognition')
  const identity = params.get('session')
  expect(identity).toBeTruthy()
  const session = await (await networkFetch(origin + base + '/' + identity)).json()
  expect(session.trials).toHaveLength(2)
  expect(session.trials[0].answer).toBe(String(expected))
  expect(session.trials[0].correct).toBe(true)
  expect(session.trials[1].correct).toBe(false)
  expect(session.trials[0].elapsed_ms).toBeGreaterThanOrEqual(0)
  expect(session.trials[0].expected).toBeUndefined()
  expect(session.current_trial).toBeNull()
  expect(session.score.accuracy).toBe(0.5)
  view.unmount()
  render(<Cognition />)
  await screen.findByRole('heading', { name: 'Session: completed' })
  expect(screen.getByText('Accuracy: 50%')).toBeInTheDocument()
  expect(screen.getByText(/Trial 1:.*correct/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'New exercise' }))
  await screen.findByRole('heading', { name: 'Start a session' })
  expect(new URLSearchParams(location.hash.split('?')[1]).has('session')).toBe(false)
  expect(new URLSearchParams(location.hash.split('?')[1]).get('shell')).toBe('retained')
})

test('ink color response and cancellation retain real trial result', async () => {
  open()
  await screen.findByRole('heading', { name: 'Start a session' })
  change('Exercise kind', 'color_word')
  change('Number of trials (1–20)', '2')
  fireEvent.click(screen.getByRole('button', { name: 'Start exercise' }))
  await screen.findByRole('heading', { name: 'Trial 1' })
  const stimulus = screen.getByLabelText('Color word stimulus')
  const ink = stimulus.getAttribute('data-color')!
  expect(['red', 'blue', 'green', 'yellow']).toContain(ink)
  expect(stimulus.style.color).not.toBe('')
  expect(['red', 'blue', 'green', 'yellow']).toContain(stimulus.textContent)
  fireEvent.click(screen.getByRole('button', { name: ink, exact: true }))
  await screen.findByRole('heading', { name: 'Trial 2' })
  expect(screen.getByText('Answered: 1 · Correct: 1')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel exercise' }))
  await screen.findByRole('heading', { name: 'Session: cancelled' })
  expect(screen.queryByLabelText('Color word stimulus')).not.toBeInTheDocument()
  expect(screen.getByText('Accuracy: 100%')).toBeInTheDocument()
  const identity = new URLSearchParams(location.hash.split('?')[1]).get('session')
  const row = await (await networkFetch(origin + base + '/' + identity)).json()
  expect(row.status).toBe('cancelled')
  expect(row.trials).toHaveLength(1)
  expect(row.trials[0].answer).toBe(ink)
  expect(row.trials[0].correct).toBe(true)
  expect(row.score.mean_elapsed_ms).toBe(row.trials[0].elapsed_ms)
  expect(row.revision).toBe(3)
})

test('real deadline refresh and validation never invent answered trials', async () => {
  open()
  await screen.findByRole('heading', { name: 'Start a session' })
  change('Number of trials (1–20)', '0')
  fireEvent.click(screen.getByRole('button', { name: 'Start exercise' }))
  expect((await screen.findByRole('alert')).textContent).toContain('planned_trials')
  const before = await (await networkFetch(origin + base)).json()
  change('Number of trials (1–20)', '1')
  change('Time limit in seconds (1–600)', '1')
  fireEvent.click(screen.getByRole('button', { name: 'Start exercise' }))
  await screen.findByRole('heading', { name: 'Session: expired' }, { timeout: 6000 })
  expect(screen.getByText('Answered: 0 · Correct: 0')).toBeInTheDocument()
  expect(screen.getByText('Accuracy: Not recorded')).toBeInTheDocument()
  expect(screen.getByText('Mean response time: Not recorded')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Submit answer' })).not.toBeInTheDocument()
  const identity = new URLSearchParams(location.hash.split('?')[1]).get('session')
  const row = await (await networkFetch(origin + base + '/' + identity)).json()
  expect(row.status).toBe('expired')
  expect(row.trials).toEqual([])
  expect(row.current_trial).toBeNull()
  const after = await (await networkFetch(origin + base)).json()
  expect(after.sessions).toHaveLength(before.sessions.length + 1)
})
