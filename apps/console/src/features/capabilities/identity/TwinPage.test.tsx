import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { render, screen, waitFor, fireEvent, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import TwinPage from './TwinPage'

let server: ChildProcess
let endpoint = ''
let directory = ''
const root = resolve(process.cwd(), '../..')
beforeAll(async () => {
  directory = await mkdtemp(resolve(tmpdir(), 'gideon-stories-ui-'))
  server = spawn('/tmp/gideon-runtime-venv/bin/python', [
    resolve(root, 'checks/runtime/capabilities/identity/twin_ui_server.py'), resolve(directory, 'stories.sqlite3'),
  ], { env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') } })
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

test('edit durable sources, privacy, traits and selected overlay through actual HTTP', async () => {
  render(<TwinPage endpoint={endpoint} />)
  await screen.findByText('No sources yet.')
  expect(screen.getByRole('heading', { name: 'Your identity context' })).toBeVisible()
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Background' } })
  fireEvent.change(screen.getByLabelText('Source text'), { target: { value: 'I study astronomy.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save source' }))
  await screen.findByRole('button', { name: 'Background' })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save identity settings' })).toBeEnabled())
  fireEvent.click(screen.getByLabelText('Use identity in private conversations'))
  fireEvent.change(screen.getByLabelText('Traits (JSON object)'), { target: { value: '{"curiosity":9}' } })
  fireEvent.change(screen.getByLabelText('Persona overlays (JSON list)'), { target: { value: JSON.stringify([
    { id: 'work', name: 'Work', instructions: 'Prefers concise facts', trait_adjustments: { concision: 8 } },
  ]) } })
  fireEvent.change(screen.getByLabelText('Active overlay ID'), { target: { value: 'work' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save identity settings' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview shared context' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview shared context' }))
  const preview = await screen.findByRole('region', { name: 'Identity preview' })
  expect(preview).toHaveTextContent('I study astronomy.')
  expect(preview).toHaveTextContent('curiosity')
  expect(preview).toHaveTextContent('Prefers concise facts')
  expect(preview).toHaveTextContent('not agent identity or operating instructions')
  fireEvent.click(screen.getByRole('button', { name: 'Background' }))
  expect(screen.getByLabelText('Source text')).toHaveValue('I study astronomy.')
  fireEvent.click(screen.getByLabelText('Private source'))
  fireEvent.click(screen.getByRole('button', { name: 'Save source' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview shared context' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview shared context' }))
  await waitFor(() => expect(screen.getByRole('region', { name: 'Identity preview' })).not.toHaveTextContent('I study astronomy.'))
  expect(screen.getByRole('button', { name: 'Suggest questions' })).toBeDisabled()
  expect(screen.getByLabelText('Private source')).toBeChecked()
  fireEvent.change(screen.getByLabelText('Traits (JSON object)'), { target: { value: 'bad-json' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save identity settings' }))
  await screen.findByRole('alert')
  expect(screen.getByLabelText('Traits (JSON object)')).toHaveValue('bad-json')
  fireEvent.click(screen.getByRole('button', { name: 'Reload identity' }))
  await waitFor(() => expect((screen.getByLabelText('Traits (JSON object)') as HTMLTextAreaElement).value).toContain('curiosity'))
  const response = await fetch(endpoint)
  const persisted = await response.json()
  expect(persisted.documents).toHaveLength(1)
  expect(persisted.documents[0].private).toBe(true)
  expect(persisted.traits).toEqual({ curiosity: 9 })
  expect(persisted.active_persona_id).toBe('work')
  fireEvent.change(screen.getByLabelText('Context token budget (conservative)'), { target: { value: '1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview shared context' }))
  await waitFor(() => expect(screen.getByRole('region', { name: 'Identity preview' })).toHaveTextContent('Sources omitted for budget:'))
  expect(screen.getByRole('region', { name: 'Identity preview' })).not.toHaveTextContent('curiosity')
  fireEvent.click(screen.getByRole('button', { name: 'Delete source' }))
  await screen.findByText('No sources yet.')
  expect(await (await fetch(endpoint)).json()).toMatchObject({ documents: [], traits: { curiosity: 9 } })
})

test('stale document saves retain the draft and show real conflict response', async () => {
  const created = await fetch(endpoint)
  const before = await created.json()
  const seeded = await fetch(endpoint + '/documents', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: 'Current work', text: 'Building a telescope.', expected_revision: before.revision }) })
  expect(seeded.status).toBe(200)
  const state = await seeded.json()
  const doc = state.documents.find((row: { title: string }) => row.title === 'Current work')
  window.location.hash = '#/capabilities/identity/twin?document=' + doc.id
  render(<TwinPage endpoint={endpoint} />)
  await waitFor(() => expect(screen.getByLabelText('Source text')).toHaveValue('Building a telescope.'))
  const external = await fetch(endpoint + '/documents', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...doc, text: 'Completed a telescope.', expected_revision: state.revision }) })
  expect(external.status).toBe(200)
  fireEvent.change(screen.getByLabelText('Source text'), { target: { value: 'My unsaved draft.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save source' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Identity changed; reload before saving')
  expect(screen.getByLabelText('Source text')).toHaveValue('My unsaved draft.')
  fireEvent.click(screen.getByRole('button', { name: 'Reload identity' }))
  await waitFor(() => expect(screen.getByLabelText('Source text')).toHaveValue('Completed a telescope.'))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
