import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import ProgressPage from './ProgressPage'

let server: ChildProcess
let endpoint = ''
let directory = ''
const root = resolve(process.cwd(), '../..')
const nativeFetch = globalThis.fetch
beforeAll(async () => {
  directory = await mkdtemp(resolve(tmpdir(), 'gideon-stories-ui-'))
  server = spawn(process.env.GIDEON_TEST_PYTHON || resolve(root, '.venv/bin/python'), [
    resolve(root, 'checks/runtime/capabilities/identity/progress_ui_server.py'), resolve(directory, 'capabilities/identity/progress.sqlite3'),
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
      resolveEndpoint(`${origin}/api/capabilities/identity/progress`)
    })
    server.once('error', error => { clearTimeout(timer); reject(error) })
    server.once('exit', code => { clearTimeout(timer); reject(new Error('HTTP server exited ' + code + ': ' + errors)) })
  })
  const refused = await nativeFetch(endpoint)
  expect(refused.status).toBe(403)
  expect(await refused.json()).toMatchObject({ error: 'Token required' })
  expect((await fetch(endpoint)).status).toBe(200)
})
afterEach(() => { cleanup(); window.location.hash = '' })
afterAll(async () => {
  globalThis.fetch = nativeFetch
  if (server && server.exitCode === null) {
    await new Promise<void>(resolveExit => { server.once('exit', () => resolveExit()); server.kill('SIGTERM') })
  }
  await rm(directory, { recursive: true, force: true })
})

test('unknown progress, canonical birthday, missing task source and reload through actual HTTP', async () => {
  window.location.hash = '#/capabilities/identity/progress?as_of=2026-10-01'
  render(<ProgressPage endpoint={endpoint} />)
  await screen.findByText('Age: Unknown')
  expect(screen.getByText(/Health and skill level are unknown/)).toBeVisible()
  expect(screen.getByRole('region', { name: 'Progress summary' })).toHaveTextContent('Planned minutes in completed sessions: 0')
  fireEvent.change(screen.getByLabelText('Birth date (optional)'), { target: { value: '2000-10-02' } })
  fireEvent.change(screen.getByLabelText('Timezone'), { target: { value: 'Europe/Berlin' } })
  fireEvent.change(screen.getByLabelText('Tracked native task IDs (one per line)'), { target: { value: 't-not-present' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save progress settings' }))
  await screen.findByText('Age: 25')
  expect(screen.getByText('Missing source: t-not-present')).toBeVisible()
  const current = await (await fetch(endpoint + '?as_of=2026-10-01')).json()
  expect(current.profile.birth_date).toBe('2000-10-02')
  expect(current.profile.timezone).toBe('Europe/Berlin')
  expect(current.profile.tracked_task_ids).toEqual(['t-not-present'])
  expect(current.tasks_done).toBe(0)
  fireEvent.change(screen.getByLabelText('As of local date'), { target: { value: '2026-10-02' } })
  fireEvent.click(screen.getByRole('button', { name: 'Reload progress' }))
  await screen.findByText('Age: 26')
  expect(window.location.hash).toContain('as_of=2026-10-02')
  cleanup()
  render(<ProgressPage endpoint={endpoint} />)
  await screen.findByText('Age: 26')
  expect(screen.getByLabelText('Birth date (optional)')).toHaveValue('2000-10-02')
  expect(screen.getByLabelText('Timezone')).toHaveValue('Europe/Berlin')
  fireEvent.change(screen.getByLabelText('Timezone'), { target: { value: 'Invalid/Timezone' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save progress settings' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('IANA timezone')
  expect(screen.getByLabelText('Timezone')).toHaveValue('Invalid/Timezone')
  fireEvent.click(screen.getByRole('button', { name: 'Reload progress' }))
  await waitFor(() => expect(screen.getByLabelText('Timezone')).toHaveValue('Europe/Berlin'))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Birth date (optional)'), { target: { value: '' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save progress settings' }))
  await screen.findByText('Age: Unknown')
  const cleared = await (await fetch(endpoint)).json()
  expect(cleared.profile.birth_date).toBeNull()
  expect(cleared.unknown_metrics).toContain('measured_effort')
})
