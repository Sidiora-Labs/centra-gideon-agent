import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import Page from './Page'

let server: ChildProcess
let origin: string
let home: string
const originalFetch = globalThis.fetch
const base = '/api/capabilities/communications/people'

beforeAll(async () => {
  home = mkdtempSync(resolve(tmpdir(), 'gideon-people-ui-'))
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/communications/ui_server.py'], {
    cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  origin = await new Promise<string>((resolveOrigin, reject) => {
    let output = ''
    let errors = ''
    server.stderr?.on('data', chunk => { errors += String(chunk) })
    server.stdout?.on('data', chunk => {
      output += String(chunk)
      const line = output.split('\n').find(value => value.startsWith('{"port":'))
      if (line) resolveOrigin(`http://127.0.0.1:${JSON.parse(line).port}`)
    })
    server.on('error', reject)
    server.on('exit', code => reject(new Error(`HTTP server exited ${code}: ${errors}`)))
  })
  globalThis.fetch = (input, init) => originalFetch(typeof input === 'string' && input.startsWith('/') ? origin + input : input, init)
})

afterAll(() => {
  cleanup()
  globalThis.fetch = originalFetch
  server?.kill()
  rmSync(home, { recursive: true, force: true })
})

it('creates, edits, records contact and reopens source-backed detail through actual HTTP', async () => {
  location.hash = '#/capabilities/communications'
  const view = render(<Page />)
  await screen.findByText('No people yet. Add someone to begin tracking contact.')
  expect(screen.getByRole('button', { name: 'Save person' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Example Friend' } })
  fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Remember their exhibition.' } })
  fireEvent.change(screen.getByLabelText(/Identities/), { target: { value: 'email:friend@example.com\nphone:+12025550123' } })
  fireEvent.change(screen.getByLabelText('Cadence in days'), { target: { value: '7' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save person' }))
  await screen.findByText('Edit person')
  expect(location.hash).toContain('?person=')
  const id = new URLSearchParams(location.hash.split('?')[1]).get('person')!
  const createdResponse = await originalFetch(`${origin}${base}/${id}`)
  expect(createdResponse.status).toBe(200)
  const created = await createdResponse.json()
  expect(created.person.name).toBe('Example Friend')
  expect(created.person.notes).toBe('Remember their exhibition.')
  expect(created.person.identities).toEqual([{ kind: 'email', value: 'friend@example.com' }, { kind: 'phone', value: '+12025550123' }])
  expect(created.person.cadence_days).toBe(7)
  expect(created.care.state).toBe('missing')
  fireEvent.change(screen.getByLabelText('Occurred at (ISO with timezone)'), { target: { value: '2020-01-01T12:00:00+02:00' } })
  fireEvent.change(screen.getByLabelText('Contact summary'), { target: { value: 'Discussed exhibition plans.' } })
  fireEvent.change(screen.getByLabelText('Direction'), { target: { value: 'outbound' } })
  fireEvent.click(screen.getByRole('button', { name: 'Record contact' }))
  expect(await screen.findByText('Care: overdue')).toBeInTheDocument()
  expect(screen.getByText('Discussed exhibition plans.')).toBeInTheDocument()
  const detailResponse = await originalFetch(`${origin}${base}/${id}`)
  const detail = await detailResponse.json()
  expect(detail.touchpoints).toHaveLength(1)
  expect(detail.touchpoints[0].occurred_at).toBe('2020-01-01T10:00:00+00:00')
  expect(detail.touchpoints[0].direction).toBe('outbound')
  expect(detail.touchpoints[0].source).toBe('manual')
  fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Notes updated after contact.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save person' }))
  await waitFor(async () => {
    const response = await originalFetch(`${origin}${base}/${id}`)
    expect((await response.json()).person.revision).toBe(2)
  })
  view.unmount()
  render(<Page />)
  await screen.findByText('Edit person')
  expect(screen.getByLabelText('Notes')).toHaveValue('Notes updated after contact.')
  expect(screen.getByText('Discussed exhibition plans.')).toBeInTheDocument()
  expect(screen.getByLabelText('Name')).toHaveValue('Example Friend')
  expect(screen.getByRole('link', { name: 'Example Friend' })).toHaveAttribute('href', `#/capabilities/communications?person=${id}`)
  cleanup()
})

it('retains editing input when actual server rejects a stale revision', async () => {
  const response = await originalFetch(origin + base)
  const people = (await response.json()).people
  expect(people).toHaveLength(1)
  const person = people[0]
  location.hash = `#/capabilities/communications?person=${person.id}`
  render(<Page />)
  await screen.findByText('Edit person')
  const update = await originalFetch(`${origin}${base}/${person.id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: person.name, notes: 'Changed elsewhere', ring: person.ring, cadence_days: person.cadence_days, identities: person.identities, revision: person.revision }),
  })
  expect(update.status).toBe(200)
  fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Unsaved local edit' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save person' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/changed|reload/i)
  expect(screen.getByLabelText('Notes')).toHaveValue('Unsaved local edit')
  const durableResponse = await originalFetch(`${origin}${base}/${person.id}`)
  expect((await durableResponse.json()).person.notes).toBe('Changed elsewhere')
  fireEvent.click(screen.getByRole('button', { name: 'Reload' }))
  await waitFor(() => expect(screen.getByLabelText('Notes')).toHaveValue('Changed elsewhere'))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  cleanup()
})

it('shows invalid identity errors without claiming a saved person', async () => {
  location.hash = '#/capabilities/communications'
  render(<Page />)
  await screen.findByText('Add person')
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Invalid identity example' } })
  fireEvent.change(screen.getByLabelText(/Identities/), { target: { value: 'email:invalid' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save person' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/Invalid email/)
  expect(screen.getByLabelText('Name')).toHaveValue('Invalid identity example')
  const response = await originalFetch(origin + base)
  expect((await response.json()).people).toHaveLength(1)
  expect(location.hash).toBe('#/capabilities/communications')
  cleanup()
})
