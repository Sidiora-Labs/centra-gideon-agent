import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { fireEvent, render, screen, within, waitFor } from '@testing-library/react'
import { useHashRoute } from '../../../app/shell/useHashRoute'
import Page from './Page'

let server: ChildProcess
let baseUrl: string
let home: string
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-api-explorer-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/ui_server.py'], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home, GIDEON_DEV_NO_AUTH: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
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
  if (server && server.exitCode === null) {
    await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  }
  await rm(home, { recursive: true, force: true })
})

function Location() { return <output aria-label="Location">{JSON.stringify(useHashRoute('capabilities').query)}</output> }
function show(entry = '/capabilities/platform', origin = baseUrl) {
  history.replaceState(null, '', `#${entry}`)
  return render(<><Page baseUrl={origin} /><Location /></>)
}

describe('API explorer over real dashboard HTTP', () => {
  it('assembles the qualified platform slices over their real mounted HTTP routes', async () => {
    show()
    for (const [view, label, path] of [
      ['Providers', 'Subscription quota plans', '/api/capabilities/platform/quotas/plans'],
      ['Integrations', 'Integration applications', '/api/capabilities/platform/integration-apps'],
      ['Network', 'Remote agent sessions', '/api/capabilities/platform/remote-sessions'],
      ['Network', 'Direct peers', '/api/capabilities/platform/peers'],
      ['Network', 'Domain replication', '/api/capabilities/platform/replication'],
      ['Sharing', 'Remote media execution', '/api/capabilities/platform/remote-media'],
      ['Sharing', 'Selective media sharing', '/api/capabilities/platform/media-shares'],
      ['Archive migration', 'Archive migration', '/api/capabilities/platform/migration'],
    ]) {
      fireEvent.click(screen.getByRole('button', { name: view }))
      expect(await screen.findByRole('region', { name: label })).toBeVisible()
      const response = await fetch(baseUrl + path, { headers: { 'X-Session-Key': 'dashboard:ui' } })
      expect(response.status, path).toBe(200)
      if (path !== '/api/capabilities/platform/migration') expect(response.headers.get('cache-control'), path).toBe('no-store')
    }
    fireEvent.click(screen.getByRole('button', { name: 'Network' }))
    expect(await screen.findByText('No remote agent connections configured.')).toBeVisible()
    expect(await screen.findByText('No direct peers configured.')).toBeVisible()
    expect(await screen.findByText('No unresolved replication conflicts.')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Sharing' }))
    expect(await screen.findByText('No remote executions recorded.')).toBeVisible()
    expect(await screen.findByText('No media has been shared.')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Archive migration' }))
    expect(await screen.findByText('No archive imports recorded.')).toBeVisible()
  })

  it('loads registered methods, filters and preserves URL selection', async () => {
    show('/capabilities/platform?view=api')
    expect(within(screen.getByRole('main')).getByText('Loading routes…')).toBeVisible()
    const syntax = await screen.findByRole('button', { name: 'GET /api/prompts/syntax' })
    expect(syntax).toBeVisible()
    expect(screen.getByText(/registered routes; page starts at 1./)).toBeVisible()
    expect(screen.getByRole('button', { name: 'HEAD /api/prompts/syntax' })).toBeVisible()
    fireEvent.change(screen.getByLabelText('Method'), { target: { value: 'GET' } })
    await waitFor(() => expect(screen.queryByRole('button', { name: 'HEAD /api/prompts/syntax' })).toBeNull())
    expect(screen.getByLabelText('Location')).toHaveTextContent('"method":"GET"')
    fireEvent.change(screen.getByLabelText('Search routes'), { target: { value: 'prompts' } })
    await waitFor(() => expect(screen.queryByRole('button', { name: 'GET /api/capabilities/platform/catalog' })).toBeNull())
    fireEvent.click(syntax)
    expect(await screen.findByRole('region', { name: 'Route detail' })).toHaveTextContent('api_prompt_syntax')
    expect(screen.getByLabelText('Location')).toHaveTextContent('"route":"GET')
    expect(screen.getByRole('region', { name: 'Route detail' })).toHaveTextContent('Schema: unknown')
    expect(screen.getByRole('button', { name: 'Execute read' })).not.toHaveAttribute('aria-disabled', 'true')
  })

  it('executes a real harmless request and shows declared event keys', async () => {
    show('/capabilities/platform?route=GET+%2Fapi%2Fprompts%2Fsyntax')
    const execute = await screen.findByRole('button', { name: 'Execute read' })
    fireEvent.click(execute)
    const response = await screen.findByLabelText('HTTP response')
    expect(response).toHaveTextContent('HTTP 200')
    expect(response).toHaveTextContent('upper')
    expect(response).toHaveTextContent('replace')
    const events = screen.getByRole('region', { name: 'Declared events' })
    expect(events).toHaveTextContent('session.created')
    expect(events).toHaveTextContent('knowledge.ingested')
    expect(events).toHaveTextContent('task.completed')
    expect(events).toHaveTextContent('item_id, status')
    expect(events).toHaveTextContent('value types unspecified')
  })

  it('disables unapproved methods and renders empty filters', async () => {
    show('/capabilities/platform?route=HEAD+%2Fapi%2Fprompts%2Fsyntax')
    const execute = await screen.findByRole('button', { name: 'Execute read' })
    expect(execute).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(execute)
    expect(screen.queryByLabelText('HTTP response')).toBeNull()
    fireEvent.change(screen.getByLabelText('Method'), { target: { value: 'PATCH' } })
    expect(await screen.findByText('No matching routes')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Previous' })).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByRole('button', { name: 'Next' })).toHaveAttribute('aria-disabled', 'true')
    const detail = screen.getByRole('region', { name: 'Route detail' })
    expect(within(detail).getByRole('heading')).toHaveTextContent('HEAD /api/prompts/syntax')
  })

  it('shows real HTTP catalog errors and offers retry', async () => {
    show('/capabilities/platform?offset=-1')
    expect(await screen.findByText(/offset must be/)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Execute read' })).toBeNull()
    const retry = screen.getByRole('button', { name: 'Refresh' })
    fireEvent.click(retry)
    expect(await screen.findByText(/offset must be/)).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Declared events' })).toBeNull()
  })
})


it('preserves real hash route selection across remount and external hash navigation', async () => {
  const view = show('/capabilities/platform?keep=1')
  fireEvent.click(await screen.findByRole('button', { name: 'GET /api/prompts/syntax' }))
  await screen.findByRole('region', { name: 'Route detail' })
  expect(location.hash).toContain('/capabilities/platform?')
  expect(location.hash).toContain('keep=1')
  expect(location.hash).toContain('route=GET')
  const selected = location.hash.slice(1)
  view.unmount()
  show(selected)
  expect(await screen.findByRole('region', { name: 'Route detail' })).toHaveTextContent('GET /api/prompts/syntax')
  location.hash = '#/capabilities/platform?method=HEAD&keep=1'
  await waitFor(() => expect(screen.getByLabelText('Method')).toHaveValue('HEAD'))
  expect(screen.queryByRole('region', { name: 'Route detail' })).toBeNull()
  expect(screen.getByLabelText('Location')).toHaveTextContent('"keep":"1"')
})
