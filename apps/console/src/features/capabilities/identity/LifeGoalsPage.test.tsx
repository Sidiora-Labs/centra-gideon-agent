import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import LifeGoalsPage from './LifeGoalsPage'

let server: ChildProcess
let endpoint = ''
let directory = ''
const root = resolve(process.cwd(), '../..')
const nativeFetch = globalThis.fetch
beforeAll(async () => {
  directory = await mkdtemp(resolve(tmpdir(), 'gideon-stories-ui-'))
  server = spawn(process.env.GIDEON_TEST_PYTHON || resolve(root, '.venv/bin/python'), [
    resolve(root, 'checks/runtime/capabilities/identity/goals_ui_server.py'), resolve(directory, 'stories.sqlite3'),
  ], { env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: directory } })
  endpoint = await new Promise<string>((resolveEndpoint, reject) => {
    let output = ''
    let errors = ''
    const timer = setTimeout(() => reject(new Error('HTTP server startup timed out: ' + errors)), 10000)
    server.stderr?.on('data', chunk => { errors += String(chunk) })
    server.stdout?.on('data', chunk => {
      output += String(chunk)
      const line = output.split('\n').find(value => value.startsWith('{') && value.endsWith('}'))
      if (!line) return
      const ready = JSON.parse(line) as { port: number; token: string }
      const origin = `http://127.0.0.1:${ready.port}`
      globalThis.fetch = (input, init) => {
        const requestUrl = input instanceof Request ? input.url : String(input)
        const headers = new Headers(input instanceof Request ? input.headers : undefined)
        new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
        if (new URL(requestUrl, window.location.href).origin === origin) headers.set('Authorization', `Bearer ${ready.token}`)
        return nativeFetch(input, { ...init, headers })
      }
      clearTimeout(timer)
      resolveEndpoint(`${origin}/api/capabilities/identity/goals`)
    })
    server.once('error', error => { clearTimeout(timer); reject(error) })
    server.once('exit', code => { clearTimeout(timer); reject(new Error('HTTP server exited ' + code + ': ' + errors)) })
  })
  const refused = await nativeFetch(endpoint + '/goals')
  expect(refused.status).toBe(403)
  expect(await refused.json()).toMatchObject({ error: 'Token required' })
  expect((await fetch(endpoint + '/goals')).status).toBe(200)
})
afterEach(() => { cleanup(); window.location.hash = '' })
afterAll(async () => {
  globalThis.fetch = nativeFetch
  if (server && server.exitCode === null) {
    await new Promise<void>(resolveExit => { server.once('exit', () => resolveExit()); server.kill('SIGTERM') })
  }
  await rm(directory, { recursive: true, force: true })
})

test('author human goal, schedule, resolve conflicts and export a real calendar', async () => {
  render(<LifeGoalsPage endpoint={endpoint} />)
  await screen.findByText('No life goals yet.')
  expect(screen.getByRole('button', { name: 'Save session' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Goal title'), { target: { value: 'Learn astronomy' } })
  fireEvent.change(screen.getByLabelText('Why this matters'), { target: { value: 'Understand the sky' } })
  fireEvent.change(screen.getByLabelText('Target date'), { target: { value: '2027-01-01' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save goal' }))
  await screen.findByRole('button', { name: 'Learn astronomy · active' })
  const goals = await (await fetch(endpoint + '/goals')).json()
  expect(goals).toHaveLength(1)
  expect(goals[0].description).toBe('Understand the sky')
  expect(window.location.hash).toContain(goals[0].id)
  fireEvent.change(screen.getByLabelText('Session title'), { target: { value: 'Observe stars' } })
  fireEvent.change(screen.getByLabelText('Start with timezone'), { target: { value: '2026-10-01T21:00:00+02:00' } })
  fireEvent.change(screen.getByLabelText('End with timezone'), { target: { value: '2026-10-01T22:00:00+02:00' } })
  fireEvent.change(screen.getByLabelText('Session notes'), { target: { value: 'Bring telescope' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save session' }))
  await screen.findByRole('button', { name: /Observe stars · scheduled/ })
  const sessions = await (await fetch(endpoint + '/sessions')).json()
  expect(sessions).toHaveLength(1)
  expect(sessions[0].start_at).toBe('2026-10-01T19:00:00.000000+00:00')
  expect(sessions[0].goal_id).toBe(goals[0].id)
  fireEvent.change(screen.getByLabelText('Goal status'), { target: { value: 'completed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save goal' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Resolve scheduled sessions')
  expect(screen.getByLabelText('Goal status')).toHaveValue('completed')
  fireEvent.change(screen.getByLabelText('Session status'), { target: { value: 'completed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save session' }))
  await screen.findByRole('button', { name: /Observe stars · completed/ })
  fireEvent.change(screen.getByLabelText('Goal status'), { target: { value: 'completed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save goal' }))
  await screen.findByRole('button', { name: 'Learn astronomy · completed' })
  fireEvent.click(screen.getByRole('button', { name: 'Export calendar' }))
  const calendar = await screen.findByRole('region', { name: 'Calendar export' })
  expect(calendar).toHaveTextContent('SUMMARY:Observe stars')
  expect(calendar).toHaveTextContent('DTSTART:20261001T190000Z')
  const link = screen.getByRole('link', { name: 'Download calendar file' })
  expect(link).toHaveAttribute('download', 'human-plans.ics')
  expect(decodeURIComponent(link.getAttribute('href') || '')).toContain('DESCRIPTION:Bring telescope')
  cleanup()
  render(<LifeGoalsPage endpoint={endpoint} />)
  await waitFor(() => expect(screen.getByLabelText('Goal title')).toHaveValue('Learn astronomy'))
  expect(screen.getByLabelText('Goal status')).toHaveValue('completed')
  expect(screen.getByLabelText('Target date')).toHaveValue('2027-01-01')
  fireEvent.click(screen.getByRole('button', { name: /Observe stars · completed/ }))
  expect(screen.getByLabelText('Session notes')).toHaveValue('Bring telescope')
  expect(screen.getByLabelText('Session status')).toHaveValue('completed')
})

test('stale HTTP saves preserve unsaved goal draft and reload current state', async () => {
  const response = await fetch(endpoint + '/goals', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: 'Fitness', request_id: 'fitness' }) })
  const goal = await response.json()
  window.location.hash = '#/capabilities/identity/goals?goal=' + goal.id
  render(<LifeGoalsPage endpoint={endpoint} />)
  await waitFor(() => expect(screen.getByLabelText('Goal title')).toHaveValue('Fitness'))
  const external = await fetch(endpoint + '/goals', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: goal.id, title: 'Daily exercise', expected_revision: 1, request_id: 'external' }) })
  expect(external.status).toBe(200)
  fireEvent.change(screen.getByLabelText('Goal title'), { target: { value: 'My draft' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save goal' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Planning record changed')
  expect(screen.getByLabelText('Goal title')).toHaveValue('My draft')
  fireEvent.click(screen.getByRole('button', { name: 'Reload plans' }))
  await waitFor(() => expect(screen.getByLabelText('Goal title')).toHaveValue('Daily exercise'))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
