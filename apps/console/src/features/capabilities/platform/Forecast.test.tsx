import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import Forecast from './Forecast'
let server: ChildProcess
let baseUrl: string
let home: string
const originalFetch = globalThis.fetch
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-forecast-`)
  const root = resolve(process.cwd(), '../..')
  const runtimeEnv: NodeJS.ProcessEnv = { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home }
  delete runtimeEnv.GIDEON_DEV_NO_AUTH
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/forecast_ui_server.py'], {
    cwd: root, env: runtimeEnv, stdio: ['ignore', 'pipe', 'pipe'],
  })
  let diagnostics = ''
  server.stderr!.on('data', chunk => { diagnostics += chunk.toString() })
  const ready = await new Promise<{ port: number; token: string }>((accept, reject) => {
    const lines = createInterface({ input: server.stdout! })
    lines.on('line', line => { if (line.startsWith('{')) { try { accept(JSON.parse(line)); lines.close() } catch (error) { reject(error) } } })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP process exited ${code}: ${diagnostics}`)))
  })
  baseUrl = `http://127.0.0.1:${ready.port}`
  expect((await originalFetch(baseUrl + '/api/capabilities/platform/forecast')).status).toBe(403)
  globalThis.fetch = (input, init) => {
    const target = typeof input === 'string' && input.startsWith('/') ? baseUrl + input : input
    const url = target instanceof Request ? target.url : String(target)
    const headers = new Headers(target instanceof Request ? target.headers : undefined)
    new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
    if (new URL(url).origin === baseUrl) headers.set('Authorization', `Bearer ${ready.token}`)
    return originalFetch(target, { ...init, headers })
  }
})
afterAll(async () => {
  globalThis.fetch = originalFetch
  if (server && server.exitCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  await rm(home, { recursive: true, force: true })
})
it('shows real scheduled occurrences, conditional admission and existing editor links', async () => {
  render(<Forecast baseUrl={baseUrl} />)
  await waitFor(() => expect(screen.getAllByRole('link', { name: 'Forecast audit' })).toHaveLength(6))
  expect(screen.getByText(/Conditional clock forecast/)).toBeVisible()
  expect(screen.getByText('manual-one: event_driven')).toBeVisible()
  expect(screen.getAllByText('Currently eligible; future execution conditional')).toHaveLength(6)
  expect(screen.getAllByRole('link', { name: 'Forecast audit' })[0]).toHaveAttribute('href', '#/triggers?open=schedule%3Aforecast')
  fireEvent.change(screen.getByLabelText('Forecast horizon'), { target: { value: '86400' } })
  expect(await screen.findByRole('status')).toHaveTextContent('Forecast limit reached')
  expect(screen.getAllByRole('link', { name: 'Forecast audit' })).toHaveLength(20)
  const actual = await (await fetch(`${baseUrl}/api/capabilities/platform/forecast?horizon=86400`)).json()
  expect(actual.events).toHaveLength(20)
  expect(actual.truncated).toBe(true)
  expect(actual.events[0].duration_seconds).toBeNull()
})
it('refreshes actual source changes without creating executions or claims', async () => {
  render(<Forecast baseUrl={baseUrl} />)
  await waitFor(() => expect(screen.getAllByRole('link', { name: 'Forecast audit' })).toHaveLength(6))
  const path = `${home}/triggers.json`
  const data = JSON.parse(await readFile(path, 'utf8'))
  const row = data.triggers.find((item: { id: string }) => item.id === 'schedule:forecast')
  row.spec.interval_secs = 1200
  await writeFile(path, JSON.stringify(data))
  const source = await readFile(path, 'utf8')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh forecast' }))
  await waitFor(() => expect(screen.getAllByRole('link', { name: 'Forecast audit' })).toHaveLength(3))
  expect(await readFile(path, 'utf8')).toBe(source)
  expect(JSON.parse(source).triggers[0].run_count).toBe(0)
  const actual = await (await fetch(`${baseUrl}/api/capabilities/platform/forecast?horizon=3600`)).json()
  expect(actual.events).toHaveLength(3)
  expect(actual.dependencies).toEqual([])
})
