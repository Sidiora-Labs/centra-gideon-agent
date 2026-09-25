import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import Gsd from './Gsd'
let server: ChildProcess
let baseUrl: string
let home: string
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-gsd-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/gsd_ui_server.py'], {
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
async function select() {
  const option = await screen.findByRole('option', { name: 'Planning project' })
  fireEvent.change(screen.getByLabelText('GSD project'), { target: { value: (option as HTMLOptionElement).value } })
  await screen.findByRole('button', { name: 'STATE.md' })
}
it('edits original planning document and creates actual idempotent open phase request', async () => {
  render(<Gsd baseUrl={baseUrl} />)
  await select()
  expect(screen.getByText('1 plan documents · 0 summary documents')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'STATE.md' }))
  expect(await screen.findByLabelText('Planning document text')).toHaveValue('Initial planning state.\n')
  fireEvent.change(screen.getByLabelText('Planning document text'), { target: { value: 'Edited in the original planning source.\n' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save planning document' }))
  await waitFor(async () => expect(await readFile(`${home}/workspace/.planning/STATE.md`, 'utf8')).toBe('Edited in the original planning source.\n'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save planning document' })).not.toBeDisabled())
  fireEvent.click(screen.getByRole('button', { name: 'Request plan 01-editor' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Created task')
  expect(screen.getByRole('status')).toHaveTextContent('Execution has not been started')
  fireEvent.click(screen.getByRole('button', { name: 'Request plan 01-editor' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Existing task'))
  const projects = await (await fetch(`${baseUrl}/api/capabilities/platform/gsd`)).json()
  const response = await fetch(`${baseUrl}/api/capabilities/platform/gsd/${projects.projects[0].id}/phase`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ phase: '01-editor', action: 'plan' }) })
  const task = await response.json()
  expect(task.created).toBe(false)
  expect(task.task.status).toBe('open')
  expect(task.task.project).toBe('Planning project')
})
it('retains draft on real source conflict and reloads external change', async () => {
  render(<Gsd baseUrl={baseUrl} />)
  await select()
  fireEvent.click(screen.getByRole('button', { name: 'STATE.md' }))
  await screen.findByLabelText('Planning document text')
  await writeFile(`${home}/workspace/.planning/STATE.md`, 'External source change.\n')
  fireEvent.change(screen.getByLabelText('Planning document text'), { target: { value: 'Unsaved local draft.\n' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save planning document' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Document changed')
  expect(screen.getByLabelText('Planning document text')).toHaveValue('Unsaved local draft.\n')
  expect(await readFile(`${home}/workspace/.planning/STATE.md`, 'utf8')).toBe('External source change.\n')
  fireEvent.click(screen.getByRole('button', { name: 'Reload planning document' }))
  await waitFor(() => expect(screen.getByLabelText('Planning document text')).toHaveValue('External source change.\n'))
})
