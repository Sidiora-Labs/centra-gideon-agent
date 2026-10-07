import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import PrScreening from './PrScreening'
let server: ChildProcess
let baseUrl: string
let home: string
const networkFetch = globalThis.fetch
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-pr-screening-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/pr_screening_ui_server.py'], {
    cwd: root, env: { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  let diagnostics = ''
  server.stderr!.on('data', chunk => { diagnostics += chunk.toString() })
  baseUrl = await new Promise<string>((accept, reject) => {
    const lines = createInterface({ input: server.stdout! })
    lines.on('line', line => {
      if (!line.startsWith('{')) return
      try {
        const ready: { port: number; token: string } = JSON.parse(line)
        if (!Number.isInteger(ready.port) || !ready.token) throw new Error('Invalid native readiness')
        const origin = `http://127.0.0.1:${ready.port}`
        globalThis.fetch = (input, init) => {
          const url = new URL(input instanceof Request ? input.url : String(input), origin)
          const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))
          if (url.origin === origin) headers.set('Authorization', `Bearer ${ready.token}`)
          return networkFetch(input, { ...init, headers })
        }
        accept(origin)
        lines.close()
      } catch (error) { reject(error) }
    })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP process exited ${code}: ${diagnostics}`)))
  })
})
afterAll(async () => {
  globalThis.fetch = networkFetch
  if (server && server.exitCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  await rm(home, { recursive: true, force: true })
})
it('renders pinned source and real model-unavailable screening result', async () => {
  render(<PrScreening baseUrl={baseUrl} />)
  expect(await screen.findByRole('status')).toHaveTextContent('captured')
  expect(screen.getByText('Head: ' + 'a'.repeat(40))).toBeVisible()
  expect(screen.getByRole('button', { name: 'Authorize GitHub review' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Screen pull request example/project #7' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('blocked'))
  expect(screen.getByText(/Screening unavailable or invalid/)).toBeVisible()
  expect(screen.getByRole('button', { name: 'Authorize GitHub review' })).toBeDisabled()
  const value = await (await fetch(`${baseUrl}/api/capabilities/platform/pr-screening`)).json()
  expect(value.records[0].revision).toBe(2)
  expect(value.records[0].proposal).toBeNull()
  expect(value.records[0].review_id).toBeNull()
})
it('rejects actual unavailable named credential and preserves operator input', async () => {
  render(<PrScreening baseUrl={baseUrl} />)
  expect(screen.getByRole('button', { name: 'Capture pull request' })).toBeDisabled()
  await screen.findByRole('option', { name: 'github-review' })
  fireEvent.change(screen.getByLabelText('PR repository'), { target: { value: 'example/project' } })
  fireEvent.change(screen.getByLabelText('PR number'), { target: { value: '8' } })
  fireEvent.change(screen.getByLabelText('PR credential'), { target: { value: 'github-review' } })
  fireEvent.change(screen.getByLabelText('PR screening provider'), { target: { value: 'screen-api' } })
  fireEvent.change(screen.getByLabelText('PR review provider'), { target: { value: 'review-api' } })
  fireEvent.click(screen.getByRole('button', { name: 'Capture pull request' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('verify credential')
  expect(screen.getByLabelText('PR repository')).toHaveValue('example/project')
  expect(screen.getByLabelText('PR number')).toHaveValue(8)
  expect(screen.getByLabelText('PR screening provider')).toHaveValue('screen-api')
  expect(screen.getByLabelText('PR review provider')).toHaveValue('review-api')
  const value = await (await fetch(`${baseUrl}/api/capabilities/platform/pr-screening`)).json()
  expect(value.records).toHaveLength(1)
  expect(value.records[0].number).toBe(7)
  expect(value.records[0].status).toBe('blocked')
})
