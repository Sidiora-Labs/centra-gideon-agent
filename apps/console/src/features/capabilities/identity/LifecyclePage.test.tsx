import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import LifecyclePage from './LifecyclePage'

let server: ChildProcess
let endpoint = ''
let directory = ''
const root = resolve(process.cwd(), '../..')
beforeAll(async () => {
  directory = await mkdtemp(resolve(tmpdir(), 'gideon-stories-ui-'))
  server = spawn('/tmp/gideon-runtime-venv/bin/python', [
    resolve(root, 'checks/runtime/capabilities/identity/lifecycle_ui_server.py'), directory,
  ], { env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: directory } })
  endpoint = await new Promise<string>((resolveEndpoint, reject) => {
    let output = ''
    let errors = ''
    const timer = setTimeout(() => reject(new Error('HTTP server startup timed out: ' + errors)), 10000)
    server.stderr?.on('data', chunk => { errors += String(chunk) })
    server.stdout?.on('data', chunk => {
      output += String(chunk)
      const line = output.split('\n').find(value => value.startsWith('http://'))
      if (line) { clearTimeout(timer); resolveEndpoint(line.trim()) }
    })
    server.once('error', error => { clearTimeout(timer); reject(error) })
    server.once('exit', code => { clearTimeout(timer); reject(new Error('HTTP server exited ' + code + ': ' + errors)) })
  })
})
afterEach(() => { cleanup(); window.location.hash = '' })
afterAll(async () => {
  if (server && server.exitCode === null) {
    await new Promise<void>(resolveExit => { server.once('exit', () => resolveExit()); server.kill('SIGTERM') })
  }
  await rm(directory, { recursive: true, force: true })
})

test('actual loop authority, bounded thinking delivery and independent cancellation survive reload', async () => {
  const [base, loopId] = endpoint.split('#')
  endpoint = base
  render(<LifecyclePage endpoint={endpoint} />)
  await screen.findByText('Loop state: unbound')
  expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Existing loop ID'), { target: { value: loopId } })
  fireEvent.click(screen.getByLabelText('Enable bound identity and approve current route'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save lifecycle policy' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Save lifecycle policy' }))
  await screen.findByText('Loop state: ready')
  expect(screen.getByLabelText('Existing loop ID')).toBeDisabled()
  expect(screen.getByLabelText('Enable bound identity and approve current route')).toBeChecked()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Start' }))
  await screen.findByText('Loop state: running')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Request thinking' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Request thinking' }))
  await screen.findByText('reflect: pending')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Dispatch thinking request' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Dispatch thinking request' }))
  await screen.findByText('reflect: blocked · no_deliverer')
  expect(screen.getByText('Provider readiness: unknown')).toBeVisible()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel current turn' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Cancel current turn' }))
  await waitFor(async () => {
    const current = await (await fetch(endpoint)).json()
    expect(current.journal.at(-1).cancellation).toBe('idle')
  })
  expect(screen.getByText('Loop state: running')).toBeVisible()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Pause' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
  await screen.findByText('Loop state: paused')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Resume' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
  await screen.findByText('Loop state: running')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save lifecycle policy' })).toBeEnabled())
  fireEvent.click(screen.getByLabelText('Enable bound identity and approve current route'))
  fireEvent.click(screen.getByRole('button', { name: 'Save lifecycle policy' }))
  await screen.findByText('Loop state: paused')
  expect(screen.getByRole('button', { name: 'Request thinking' })).toBeDisabled()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Resume' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('disabled')
  const current = await (await fetch(endpoint)).json()
  expect(current.policy.enabled).toBe(false)
  expect(current.loop.status).toBe('paused')
  expect(current.requests).toHaveLength(1)
  expect(current.requests[0].status).toBe('blocked')
  cleanup()
  render(<LifecyclePage endpoint={endpoint} />)
  await screen.findByText('Loop state: paused')
  expect(screen.getByLabelText('Enable bound identity and approve current route')).not.toBeChecked()
  expect(screen.getByText('reflect: blocked · no_deliverer')).toBeVisible()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
  await screen.findByText('Loop state: stopped')
  expect((await (await fetch(endpoint)).json()).loop.status).toBe('stopped')
})
