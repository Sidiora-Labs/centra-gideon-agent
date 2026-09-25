import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { RemoteSessions } from './RemoteSessions'

let child: ChildProcess
let origin = ''
let home = ''
const nativeFetch = globalThis.fetch

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  home = mkdtempSync(`${tmpdir()}/gideon-remote-sessions-ui-`)
  child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/remote_sessions_ui_server.py'], { cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: `${root}/runtime:${root}/checks/runtime/capabilities/platform` }, stdio: ['ignore', 'pipe', 'pipe'] })
  await new Promise<void>((accept, reject) => {
    let output = ''; let errors = ''
    const timer = setTimeout(() => reject(new Error(errors || 'Remote sessions server timeout')), 25000)
    child.stderr!.on('data', value => { errors += String(value) })
    child.once('error', reject)
    child.stdout!.on('data', value => { output += String(value); const line = output.split('\n').find(row => row.startsWith('{"origin"')); if (line) { origin = JSON.parse(line).origin; clearTimeout(timer); accept() } })
  })
  globalThis.fetch = (input, init) => nativeFetch(new URL(String(input), origin), init)
}, 30000)

afterEach(cleanup)
afterAll(async () => {
  globalThis.fetch = nativeFetch
  if (child && child.exitCode === null) await new Promise<void>(done => { child.once('exit', () => done()); child.kill('SIGTERM') })
  if (home) rmSync(home, { recursive: true, force: true })
})

it('uses the actual HTTP protocol to retain, reopen and stream a remote session', async () => {
  render(<RemoteSessions />)
  await screen.findByRole('heading', { name: 'Browser runtime' })
  expect(screen.getByText(/credential browser-token/)).toBeTruthy()
  expect(screen.queryByText('actual-secret')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Refresh remote sessions' }))
  await screen.findByRole('button', { name: 'Actual remote' })
  expect(screen.getByText('remote')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Actual remote' }))
  await screen.findByText(/real history/)
  expect(screen.getAllByText('remote').length).toBeGreaterThan(0)
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Continue actual session' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send to remote agent' }))
  await waitFor(() => expect(screen.getByLabelText('Remote reply stream').textContent).toContain('reply'))
  await waitFor(() => expect((screen.getByLabelText('Message') as HTMLTextAreaElement).value).toBe(''))
})

it('keeps real upstream failures visible', async () => {
  render(<RemoteSessions baseUrl="http://127.0.0.1:1" />)
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('unavailable')
})
