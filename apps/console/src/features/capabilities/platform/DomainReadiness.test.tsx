import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { DomainReadiness } from './DomainReadiness'

let child: ChildProcess
let origin: string
let home: string
const nativeFetch = globalThis.fetch

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  home = mkdtempSync(`${tmpdir()}/gideon-domain-ui-`)
  const childEnv: NodeJS.ProcessEnv = { ...process.env, GIDEON_HOME: home, PYTHONPATH: `${root}/runtime:${root}` }
  delete childEnv.GIDEON_DEV_NO_AUTH
  child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/ui_server.py'], {
    cwd: root,
    env: childEnv,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  const ready = await new Promise<{ url: string; token: string }>((accept, reject) => {
    let output = ''; let errors = ''
    const timer = setTimeout(() => reject(new Error(errors || 'HTTP startup timeout')), 25000)
    child.stderr!.on('data', value => { errors += String(value) })
    child.once('error', error => { clearTimeout(timer); reject(error) })
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`HTTP process exited ${code}: ${errors}`)) })
    child.stdout!.on('data', value => {
      output += String(value)
      const line = output.split('\n').find(value => value.startsWith('{') && value.endsWith('}'))
      if (!line) return
      const payload = JSON.parse(line) as { url: string; token: string }
      if (typeof payload.url !== 'string' || typeof payload.token !== 'string') return
      clearTimeout(timer); accept(payload)
    })
  })
  origin = ready.url
  expect((await nativeFetch(origin + '/api/capabilities/platform/domain-readiness')).status).toBe(403)
  globalThis.fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), origin)
    if (url.origin !== origin) return nativeFetch(input, init)
    const headers = new Headers(init?.headers)
    headers.set('Authorization', `Bearer ${ready.token}`)
    return nativeFetch(url, { ...init, headers })
  }
}, 30000)

afterEach(cleanup)
afterAll(async () => {
  globalThis.fetch = nativeFetch
  if (child && child.exitCode === null) await new Promise<void>(done => { child.once('exit', () => done()); child.kill('SIGTERM') })
  if (home) rmSync(home, { recursive: true, force: true })
})

it('shows canonical readiness and invokes the actual scan endpoint', async () => {
  render(<DomainReadiness />)
  await screen.findByText('goals: unconfigured')
  expect(screen.getByText('wellbeing: unconfigured')).toBeTruthy()
  const detectors = screen.getByText(/Active detectors/).textContent || ''
  for (const detector of ['overdue_goal', 'recording_gap', 'unanswered_thread', 'task_quality', 'learning_health', 'recorded_crash']) {
    expect(detectors).toContain(detector)
  }
  expect(screen.getByRole('link', { name: 'Open existing notifications' }).getAttribute('href')).toBe('#/notifications')
  expect(screen.getAllByRole('link', { name: 'Open domain' }).map(row => row.getAttribute('href'))).toEqual([
    '#/capabilities/identity', '#/capabilities/wellbeing',
  ])
  fireEvent.click(screen.getByRole('button', { name: 'Check domain alerts' }))
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  const response = await fetch('/api/capabilities/platform/domain-readiness')
  expect(response.status).toBe(200)
  expect((await response.json()).domains.every((row: { state: string }) => row.state === 'unconfigured')).toBe(true)
})
