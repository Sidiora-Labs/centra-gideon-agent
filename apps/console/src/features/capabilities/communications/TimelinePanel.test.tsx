import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { TimelinePanel } from './TimelinePanel'

let server: ChildProcess
let origin: string
let home: string
const originalFetch = globalThis.fetch
const base = '/api/capabilities/communications'

beforeAll(async () => {
  home = mkdtempSync(resolve(tmpdir(), 'gideon-thread-ui-'))
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

let personId: string
it('projects real recorded touchpoints with date, provenance and person filters', async () => {
  const response = await originalFetch(origin + base + '/people', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'Timeline Alice' }) })
  personId = (await response.json()).person.id
  for (let index = 0; index < 27; index++) {
    const point = await originalFetch(origin + base + '/people/' + personId + '/touchpoints', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source: 'manual', external_id: 'timeline-' + index, occurred_at: '2026-09-25T09:00:00Z', direction: 'mutual', summary: 'Recorded conversation ' + index }) })
    expect(point.status).toBe(201)
  }
  render(<TimelinePanel />)
  await screen.findByRole('option', { name: 'Timeline Alice' })
  fireEvent.change(screen.getByLabelText('Activity date'), { target: { value: '2026-09-25' } })
  fireEvent.change(screen.getByLabelText('Activity person'), { target: { value: personId } })
  fireEvent.click(screen.getByRole('button', { name: 'Review recorded activity' }))
  await screen.findByText('Timeline coverage: available_local_records; matched records: 27')
  expect(screen.getAllByRole('link', { name: 'Open activity source' })).toHaveLength(25)
  expect(location.hash).toContain('activity_date=2026-09-25')
  const href = screen.getAllByRole('link', { name: 'Open activity source' })[0].getAttribute('href')
  expect(href).toContain('person=' + personId)
  fireEvent.click(screen.getByRole('button', { name: 'Load earlier activity' }))
  await waitFor(() => expect(screen.getAllByRole('link', { name: 'Open activity source' })).toHaveLength(27))
  expect(screen.queryByRole('button', { name: 'Load earlier activity' })).not.toBeInTheDocument()
  cleanup()
})

it('retains selected review filters in the URL and distinguishes empty filtered history', async () => {
  render(<TimelinePanel />)
  await screen.findByRole('option', { name: 'Timeline Alice' })
  expect(screen.getByLabelText('Activity date')).toHaveValue('2026-09-25')
  expect(screen.getByLabelText('Activity person')).toHaveValue(personId)
  fireEvent.change(screen.getByLabelText('Activity kind'), { target: { value: 'calendar' } })
  fireEvent.click(screen.getByRole('button', { name: 'Review recorded activity' }))
  await screen.findByText('No recorded activity matches this review.')
  expect(screen.getByText('Calendar entries are scheduled events, not proof of attendance.')).toBeInTheDocument()
  expect(screen.getByText('This is recorded communications activity, not a complete record of human activity.')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Activity kind'), { target: { value: 'touchpoint' } })
  fireEvent.click(screen.getByRole('button', { name: 'Review recorded activity' }))
  await screen.findByText('Timeline coverage: available_local_records; matched records: 27')
  expect(screen.getAllByRole('link', { name: 'Open activity source' })).toHaveLength(25)
  cleanup()
})

it('reports invalid timezone and shows known source schedules without attendance claims', async () => {
  render(<TimelinePanel />)
  await screen.findByRole('option', { name: 'Timeline Alice' })
  fireEvent.change(screen.getByLabelText('Activity timezone'), { target: { value: 'Bad/Zone' } })
  fireEvent.click(screen.getByRole('button', { name: 'Review recorded activity' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('valid timezone')
  const response = await originalFetch(origin + base + '/calendar/sources', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'Activity calendar', kind: 'ics' }) })
  const source = (await response.json()).source
  const content = 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:activity-meeting\r\nDTSTART:20260925T100000Z\r\nDTEND:20260925T110000Z\r\nSUMMARY:Planned meeting\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'
  const uploaded = await originalFetch(origin + base + '/calendar/sources/' + source.id + '/upload', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content, revision: 1 }) })
  expect(uploaded.status).toBe(200)
  fireEvent.change(screen.getByLabelText('Activity timezone'), { target: { value: 'Europe/Berlin' } })
  fireEvent.change(screen.getByLabelText('Activity person'), { target: { value: '' } })
  fireEvent.change(screen.getByLabelText('Activity kind'), { target: { value: 'calendar' } })
  fireEvent.click(screen.getByRole('button', { name: 'Review recorded activity' }))
  await screen.findByRole('heading', { name: 'Planned meeting' })
  expect(screen.getByText('calendar · calendar · scheduled_not_attendance')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open activity source' })).toHaveAttribute('href', '#/capabilities/communications?calendar_source=' + source.id)
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  cleanup()
})
