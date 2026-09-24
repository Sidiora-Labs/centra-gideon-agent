import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { ImportPanel } from './ImportPanel'

let server: ChildProcess
let origin: string
let home: string
const originalFetch = globalThis.fetch
const peoplePath = '/api/capabilities/communications/people'
let changes = 0
const refresh = () => { changes += 1 }

beforeAll(async () => {
  home = mkdtempSync(resolve(tmpdir(), 'gideon-import-ui-'))
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

it('previews without writing then commits only explicitly selected contacts', async () => {
  render(<ImportPanel onImported={refresh} />)
  expect(screen.getByRole('button', { name: 'Preview contacts' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Contact data'), { target: { value: 'name,email,notes\nImported Friend,imported@example.com,Original imported note\nSkipped Friend,skipped@example.com,Not selected' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview contacts' }))
  await screen.findByText('Imported Friend')
  expect(screen.getByText('Skipped Friend')).toBeInTheDocument()
  expect(screen.getByLabelText('Decision for contact 1')).toHaveValue('skip')
  expect(screen.getByRole('button', { name: 'Commit selected contacts' })).toBeDisabled()
  const before = await originalFetch(origin + peoplePath)
  expect((await before.json()).people).toEqual([])
  fireEvent.change(screen.getByLabelText('Decision for contact 1'), { target: { value: 'create' } })
  fireEvent.click(screen.getByRole('button', { name: 'Commit selected contacts' }))
  expect(await screen.findByText(/Import saved at/)).toBeInTheDocument()
  expect(changes).toBe(1)
  expect(screen.getByRole('button', { name: 'Commit selected contacts' })).toBeDisabled()
  expect(screen.getByText('Skipped')).toBeInTheDocument()
  const after = await originalFetch(origin + peoplePath)
  const people = (await after.json()).people
  expect(people).toHaveLength(1)
  expect(people[0].name).toBe('Imported Friend')
  expect(people[0].notes).toBe('Original imported note')
  expect(screen.getByRole('link', { name: 'Open imported person' })).toHaveAttribute('href', `#/capabilities/communications?person=${people[0].id}`)
  cleanup()
})

it('reviews a real matching person and adds identities while preserving existing notes', async () => {
  render(<ImportPanel onImported={refresh} />)
  fireEvent.change(screen.getByLabelText('Contact format'), { target: { value: 'vcard' } })
  fireEvent.change(screen.getByLabelText('Contact data'), { target: { value: 'BEGIN:VCARD\nVERSION:4.0\nFN:Replacement Name\nEMAIL:imported@example.com\nTEL:+12025550123\nNOTE:Replacement note\nEND:VCARD' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview contacts' }))
  const option = await screen.findByRole('option', { name: 'Add identities to Imported Friend' })
  const id = (option as HTMLOptionElement).value
  expect(screen.queryByRole('option', { name: 'Create person' })).not.toBeInTheDocument()
  expect(screen.getByText(/preserves existing name, notes/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Decision for contact 1'), { target: { value: id } })
  fireEvent.click(screen.getByRole('button', { name: 'Commit selected contacts' }))
  await screen.findByText(/Import saved at/)
  const response = await originalFetch(`${origin}${peoplePath}/${id}`)
  const detail = await response.json()
  expect(detail.person.notes).toBe('Original imported note')
  expect(detail.person.name).toBe('Imported Friend')
  expect(detail.person.revision).toBe(2)
  expect(detail.person.identities).toContainEqual({ kind: 'phone', value: '+12025550123' })
  expect(changes).toBe(2)
  cleanup()
})

it('refuses stale review against real HTTP and leaves contact data available to preview again', async () => {
  render(<ImportPanel onImported={refresh} />)
  fireEvent.change(screen.getByLabelText('Contact data'), { target: { value: 'name,email,phone\nThird name,imported@example.com,+12025550124' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview contacts' }))
  const option = await screen.findByRole('option', { name: 'Add identities to Imported Friend' })
  const id = (option as HTMLOptionElement).value
  const currentResponse = await originalFetch(`${origin}${peoplePath}/${id}`)
  const current = (await currentResponse.json()).person
  const updated = await originalFetch(`${origin}${peoplePath}/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: current.name, identities: current.identities, notes: 'Edited outside import', ring: current.ring, cadence_days: current.cadence_days, revision: current.revision }) })
  expect(updated.status).toBe(200)
  fireEvent.change(screen.getByLabelText('Decision for contact 1'), { target: { value: id } })
  fireEvent.click(screen.getByRole('button', { name: 'Commit selected contacts' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/changed/)
  expect(screen.queryByText(/Import saved at/)).not.toBeInTheDocument()
  expect(screen.getByLabelText('Contact data')).toHaveValue('name,email,phone\nThird name,imported@example.com,+12025550124')
  expect(changes).toBe(2)
  fireEvent.click(screen.getByRole('button', { name: 'Preview contacts' }))
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  await waitFor(() => expect(screen.getByLabelText('Decision for contact 1')).toHaveValue('skip'))
  fireEvent.change(screen.getByLabelText('Decision for contact 1'), { target: { value: id } })
  fireEvent.click(screen.getByRole('button', { name: 'Commit selected contacts' }))
  await screen.findByText(/Import saved at/)
  const finalResponse = await originalFetch(`${origin}${peoplePath}/${id}`)
  const final = (await finalResponse.json()).person
  expect(final.notes).toBe('Edited outside import')
  expect(final.identities).toContainEqual({ kind: 'phone', value: '+12025550124' })
  expect(final.revision).toBe(4)
  cleanup()
})

it('invalidates preview when the source changes and exposes malformed contacts', async () => {
  render(<ImportPanel onImported={refresh} />)
  fireEvent.change(screen.getByLabelText('Contact data'), { target: { value: 'name,email\nInvalid,not-an-email' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview contacts' }))
  expect(await screen.findByText('Invalid email identity')).toBeInTheDocument()
  expect(screen.queryByRole('option', { name: 'Create person' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Commit selected contacts' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Contact data'), { target: { value: 'name,email\nFixed,fixed@example.com' } })
  expect(screen.queryByText('Invalid email identity')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Commit selected contacts' })).not.toBeInTheDocument()
  const response = await originalFetch(origin + peoplePath)
  expect((await response.json()).people).toHaveLength(1)
  cleanup()
})
