import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import GuardedRecipesPage from './GuardedRecipesPage'

let server: ChildProcess
let endpoint = ''
let directory = ''
const root = resolve(process.cwd(), '../..')
beforeAll(async () => {
  directory = await mkdtemp(resolve(tmpdir(), 'gideon-stories-ui-'))
  server = spawn('/tmp/gideon-runtime-venv/bin/python', [
    resolve(root, 'checks/runtime/capabilities/identity/guarded_ui_server.py'), directory,
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

test('actual runtime permission request approves one side effect through the guarded console', async () => {
  render(<GuardedRecipesPage endpoint={endpoint} />)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Load session tools' })).toBeEnabled())
  fireEvent.change(screen.getByLabelText('Existing session key'), { target: { value: 'dashboard:recipes' } })
  fireEvent.click(screen.getByRole('button', { name: 'Load session tools' }))
  await screen.findByRole('option', { name: /write_file/ })
  fireEvent.change(screen.getByLabelText('Recipe title'), { target: { value: 'Write reviewed note' } })
  fireEvent.change(screen.getByLabelText('Tool 1'), { target: { value: 'write_file' } })
  fireEvent.change(screen.getByLabelText('Arguments 1'), { target: { value: JSON.stringify({ path: 'reviewed.txt', content: 'Approved actual tool output' }) } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save guarded recipe' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Save guarded recipe' }))
  await screen.findByRole('button', { name: 'Bind Write reviewed note' })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Bind Write reviewed note' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Bind Write reviewed note' }))
  await screen.findByText('Status: ready')
  expect(window.location.hash).toContain('run=')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Dispatch next step' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Dispatch next step' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh run' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Refresh run' }))
  await screen.findByRole('article', { name: 'Actual permission request' })
  expect(screen.getByText('Session approval: write_file')).toBeVisible()
  await expect(readFile(resolve(directory, 'workspace/reviewed.txt'), 'utf8')).rejects.toThrow()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Approve actual tool call' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Approve actual tool call' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh run' })).toBeEnabled())
  await waitFor(async () => expect(await readFile(resolve(directory, 'workspace/reviewed.txt'), 'utf8')).toBe('Approved actual tool output'))
  fireEvent.click(screen.getByRole('button', { name: 'Refresh run' }))
  await screen.findByText('Status: completed')
  expect(screen.getByText('first: completed')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Dispatch next step' })).toBeDisabled()
  const runId = new URLSearchParams(window.location.hash.split('?')[1]).get('run')
  const receipt = await (await fetch(endpoint + '/runs/' + runId)).json()
  expect(receipt.steps).toHaveLength(1)
  expect(receipt.session_key).toBe('dashboard:recipes')
  expect(receipt.next_index).toBe(1)
  cleanup()
  render(<GuardedRecipesPage endpoint={endpoint} />)
  await screen.findByText('Status: completed')
  expect(screen.getByText('first: completed')).toBeVisible()
  expect(await readFile(resolve(directory, 'workspace/reviewed.txt'), 'utf8')).toBe('Approved actual tool output')
})
