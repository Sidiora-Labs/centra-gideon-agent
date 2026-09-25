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
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-forecast-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/forecast_ui_server.py'], {
    cwd: root, env: { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home, GIDEON_DEV_NO_AUTH: '1' }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  let diagnostics = ''
  server.stderr!.on('data', chunk => { diagnostics += chunk.toString() })
  baseUrl = await new Promise<string>((accept, reject) => {
    const lines = createInterface({ input: server.stdout! })
    lines.on('line', line => { if (/^\d+$/.test(line)) { accept(`http://127.0.0.1:${line}`); lines.close() } })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP process exited ${code}: ${diagnostics}`)))
  })
})
afterAll(async () => {
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
