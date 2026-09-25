import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import Composition from './Composition'
import { DashboardPage } from '../../dashboard/DashboardPage'
import { ConsoleProviders } from '../../../app/bootstrap/ConsoleProviders'
import { useHashRoute } from '../../../app/shell/useHashRoute'
const browserFetch = globalThis.fetch
let server: ChildProcess
let baseUrl: string
let home: string
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-composition-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/composition_ui_server.py'], {
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
  globalThis.fetch = (input, init) => browserFetch(typeof input === 'string' ? new URL(input, baseUrl) : input, init)
})
afterAll(async () => {
  globalThis.fetch = browserFetch
  if (server && server.exitCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  await rm(home, { recursive: true, force: true })
})
it('selects and edits canonical user view membership order and size', async () => {
  render(<Composition baseUrl={baseUrl} />)
  const option = await screen.findByRole('option', { name: 'Operations' })
  fireEvent.change(screen.getByLabelText('Dashboard view'), { target: { value: (option as HTMLOptionElement).value } })
  await screen.findByLabelText('Add core widget')
  fireEvent.change(screen.getByLabelText('Add core widget'), { target: { value: 'core:tasks' } })
  await screen.findByLabelText('Size core:tasks')
  fireEvent.change(screen.getByLabelText('Size core:tasks'), { target: { value: 'full' } })
  await waitFor(() => expect(screen.getByLabelText('Size core:tasks')).toHaveValue('full'))
  await waitFor(() => expect(screen.getByLabelText('Add core widget')).not.toBeDisabled())
  fireEvent.change(screen.getByLabelText('Add core widget'), { target: { value: 'core:schedule' } })
  await screen.findByLabelText('Size core:schedule')
  fireEvent.click(screen.getByRole('button', { name: 'Move up core:schedule' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Move up core:schedule' })).toBeDisabled())
  const result = await (await fetch(`${baseUrl}/api/capabilities/platform/compositions`)).json()
  const selected = result.views.find((view: { id: string }) => view.id === result.selected_view)
  expect(selected.tiles.map((tile: { ref: string }) => tile.ref)).toEqual(['core:schedule', 'core:tasks'])
  expect(selected.tiles[1].size).toBe('full')
  expect(selected.preset).toBe(false)
})
it('creates another view and returns to persisted selection after remount', async () => {
  const mounted = render(<Composition baseUrl={baseUrl} />)
  expect(await screen.findByLabelText('Size core:tasks')).toHaveValue('full')
  fireEvent.change(screen.getByLabelText('New dashboard view'), { target: { value: 'Another view' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create dashboard view' }))
  expect(await screen.findByRole('option', { name: 'Another view' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Remove core:schedule' }))
  await waitFor(() => expect(screen.queryByLabelText('Size core:schedule')).not.toBeInTheDocument())
  mounted.unmount()
  render(<Composition baseUrl={baseUrl} />)
  expect(await screen.findByLabelText('Size core:tasks')).toHaveValue('full')
  expect(screen.queryByLabelText('Size core:schedule')).not.toBeInTheDocument()
  const result = await (await fetch(`${baseUrl}/api/capabilities/platform/compositions`)).json()
  expect(result.views.find((view: { id: string }) => view.id === result.selected_view).name).toBe('Operations')
})
function DashboardJourney() { return <DashboardPage {...useHashRoute('dashboard')} /> }
it('actual dashboard consumes selected core layout and unpins selected-view artifact only', async () => {
  const current = await (await fetch(`${baseUrl}/api/capabilities/platform/compositions`)).json()
  const pin = await fetch(`${baseUrl}/api/dashboard/views/${current.selected_view}/tiles`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ slug: 'selected-artifact' }) })
  expect(pin.status).toBe(201)
  render(<ConsoleProviders><DashboardJourney /></ConsoleProviders>)
  const grid = await screen.findByTestId('core-composition')
  expect(grid.querySelectorAll('[data-core-ref]')).toHaveLength(1)
  expect(grid.querySelector('[data-core-ref="core:tasks"]')).toHaveAttribute('data-core-size', 'full')
  expect(grid.querySelector('[data-core-ref="core:tasks"]')).toHaveClass('lg:col-span-2')
  await waitFor(() => expect(screen.getAllByRole('button', { name: 'Unpin from dashboard' })).toHaveLength(1))
  fireEvent.click(screen.getByRole('button', { name: 'Unpin from dashboard' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Unpin from dashboard' })).not.toBeInTheDocument())
  const views = await (await fetch(`${baseUrl}/api/dashboard/views`)).json()
  expect(views.views.find((view: { id: string }) => view.id === current.selected_view).tiles.some((tile: { ref: string }) => tile.ref === 'artifact:selected-artifact')).toBe(false)
  expect(views.views.find((view: { id: string }) => view.id === 'overview').tiles.some((tile: { ref: string }) => tile.ref === 'artifact:overview-only')).toBe(true)
})
