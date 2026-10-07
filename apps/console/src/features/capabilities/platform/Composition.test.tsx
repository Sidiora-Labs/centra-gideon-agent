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
let server: ChildProcess
let baseUrl: string
let home: string
const nativeFetch = globalThis.fetch
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-composition-`)
  const root = resolve(process.cwd(), '../..')
  const childEnv: NodeJS.ProcessEnv = { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home }
  delete childEnv.GIDEON_DEV_NO_AUTH
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/composition_ui_server.py'], {
    cwd: root, env: childEnv, stdio: ['ignore', 'pipe', 'pipe'],
  })
  let diagnostics = ''
  server.stderr!.on('data', chunk => { diagnostics += chunk.toString() })
  const ready = await new Promise<{ url: string; token: string }>((accept, reject) => {
    const lines = createInterface({ input: server.stdout! })
    lines.on('line', line => {
      try {
        const value = JSON.parse(line) as { url: string; token: string }
        if (typeof value.url !== 'string' || typeof value.token !== 'string') return
        accept(value); lines.close()
      } catch { /* Native readiness is the JSON line from this child. */ }
    })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP process exited ${code}: ${diagnostics}`)))
  })
  baseUrl = ready.url
  expect((await nativeFetch(`${baseUrl}/api/capabilities/platform/compositions`)).status).toBe(403)
  globalThis.fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), baseUrl)
    if (url.origin !== baseUrl) return nativeFetch(input, init)
    const headers = new Headers(init?.headers)
    headers.set('Authorization', `Bearer ${ready.token}`)
    return nativeFetch(url, { ...init, headers })
  }
})
afterAll(async () => {
  globalThis.fetch = nativeFetch
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
  await waitFor(() => {
    expect(screen.getByLabelText('Add core widget')).not.toBeDisabled()
    expect(screen.getByLabelText('Add core widget')).not.toHaveAttribute('aria-readonly', 'true')
  })
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
  const initial = await (await fetch(`${baseUrl}/api/capabilities/platform/compositions`)).json()
  const operations = initial.views.find((view: { name: string }) => view.name === 'Operations')
  const layout = await fetch(`${baseUrl}/api/capabilities/platform/compositions/${operations.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: initial.revision, tiles: [{ ref: 'core:tasks', size: 'full' }] }) })
  expect(layout.status).toBe(200)
  const layoutState = await layout.json()
  const selection = await fetch(`${baseUrl}/api/capabilities/platform/compositions/${operations.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: layoutState.revision, select: true }) })
  expect(selection.status).toBe(200)
  history.replaceState(null, '', '#/dashboard')
  const current = await (await fetch(`${baseUrl}/api/capabilities/platform/compositions`)).json()
  const pin = await fetch(`${baseUrl}/api/dashboard/views/${current.selected_view}/tiles`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ slug: 'selected-artifact' }) })
  expect(pin.status).toBe(201)
  render(<ConsoleProviders><DashboardJourney /></ConsoleProviders>)
  fireEvent.click(screen.getByText('Your overview'))
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
