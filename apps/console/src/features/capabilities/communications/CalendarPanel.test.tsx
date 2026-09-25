import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { CalendarPanel } from './CalendarPanel'

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

const calendarExport = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'BEGIN:VEVENT', 'UID:ui-meeting', 'DTSTART:20260925T090000Z', 'DTEND:20260925T100000Z', 'SUMMARY:UI calendar meeting', 'LOCATION:Office', 'END:VEVENT', 'END:VCALENDAR'].join('\r\n')

it('creates a real ICS source, imports it, and reviews the selected local day', async () => {
  render(<CalendarPanel />)
  expect(screen.getByText(/Snapshot coverage is separate/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Calendar name'), { target: { value: 'UI Calendar' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create calendar source' }))
  await screen.findByText('Calendar sync: not_synced; unknown')
  expect(location.hash).toContain('calendar_source=')
  fireEvent.change(screen.getByLabelText('ICS export content'), { target: { value: calendarExport } })
  fireEvent.click(screen.getByRole('button', { name: 'Import calendar export' }))
  await screen.findByText('Calendar sync: synced; available_snapshot')
  expect(screen.getByLabelText('ICS export content')).toHaveValue('')
  fireEvent.change(screen.getByLabelText('Review date'), { target: { value: '2026-09-25' } })
  fireEvent.change(screen.getByLabelText('Review timezone'), { target: { value: 'Europe/Berlin' } })
  fireEvent.click(screen.getByRole('button', { name: 'Review calendar day' }))
  await screen.findByText('UI calendar meeting')
  expect(screen.getByText('Review coverage: available_snapshot')).toBeInTheDocument()
  expect(screen.getByText('Office')).toBeInTheDocument()
  const response = await originalFetch(origin + base + '/calendar/daily?date=2026-09-25&timezone=Europe%2FBerlin')
  const review = await response.json()
  expect(review.events[0].uid).toBe('ui-meeting')
  expect(review.timezone).toBe('Europe/Berlin')
  expect(review.events[0].start).toBe('2026-09-25T09:00:00+00:00')
  cleanup()
})

it('retains imported data on invalid input and expands recurrence in the selected window', async () => {
  render(<CalendarPanel />)
  await screen.findByText('Calendar sync: synced; available_snapshot')
  fireEvent.change(screen.getByLabelText('ICS export content'), { target: { value: 'invalid calendar' } })
  fireEvent.click(screen.getByRole('button', { name: 'Import calendar export' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('VCALENDAR')
  expect(screen.getByLabelText('ICS export content')).toHaveValue('invalid calendar')
  const recurring = calendarExport.replace('END:VEVENT', 'RRULE:FREQ=DAILY;COUNT=3\r\nEND:VEVENT')
  fireEvent.change(screen.getByLabelText('ICS recurrence window start'), { target: { value: '2026-01-01' } })
  fireEvent.change(screen.getByLabelText('ICS recurrence window end'), { target: { value: '2027-01-01' } })
  fireEvent.change(screen.getByLabelText('ICS export content'), { target: { value: recurring } })
  fireEvent.click(screen.getByRole('button', { name: 'Import calendar export' }))
  await screen.findByText('Calendar sync: synced; available_snapshot')
  fireEvent.change(screen.getByLabelText('Review date'), { target: { value: '2026-09-26' } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Review calendar day' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Review calendar day' }))
  await screen.findByText('Review coverage: available_snapshot')
  expect(screen.getByText('UI calendar meeting')).toBeInTheDocument()
  const response = await originalFetch(origin + base + '/calendar/daily?date=2026-09-27&timezone=UTC')
  expect((await response.json()).events).toHaveLength(1)
  cleanup()
})

it('edits source settings with actual revision invalidation and then imports current source data', async () => {
  render(<CalendarPanel />)
  await screen.findByText('Calendar sync: synced; available_snapshot')
  fireEvent.click(screen.getByRole('button', { name: 'Edit calendar source' }))
  expect(screen.getByLabelText('Calendar name')).toHaveValue('UI Calendar')
  expect(screen.getByLabelText('Calendar kind')).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Calendar name'), { target: { value: 'Renamed UI Calendar' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save calendar changes' }))
  await screen.findByText('Calendar sync: not_synced; unknown')
  await screen.findByRole('option', { name: 'Renamed UI Calendar' })
  const sources = await originalFetch(origin + base + '/calendar/sources')
  const row = (await sources.json()).sources[0]
  expect(row.revision).toBe(2)
  const before = await originalFetch(origin + base + '/calendar/daily?date=2026-09-25')
  expect((await before.json()).events).toHaveLength(0)
  fireEvent.change(screen.getByLabelText('ICS export content'), { target: { value: calendarExport } })
  fireEvent.click(screen.getByRole('button', { name: 'Import calendar export' }))
  await screen.findByText('Calendar sync: synced; available_snapshot')
  cleanup()
})

it('configures a remote source and records unavailable credentials without inventing a synced calendar', async () => {
  render(<CalendarPanel />)
  await screen.findByText('Calendar sync: synced; available_snapshot')
  fireEvent.change(screen.getByLabelText('Calendar name'), { target: { value: 'Remote calendar' } })
  fireEvent.change(screen.getByLabelText('Calendar kind'), { target: { value: 'google' } })
  fireEvent.change(screen.getByLabelText('Calendar credential reference'), { target: { value: 'ABSENT_UI_CALENDAR_195D' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create calendar source' }))
  await screen.findByText('Calendar sync: not_synced; unknown')
  fireEvent.click(screen.getByRole('button', { name: 'Sync calendar week' }))
  await screen.findByText('Calendar sync: failed; unknown')
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('credential is unavailable')
  fireEvent.change(screen.getByLabelText('Review timezone'), { target: { value: 'Invalid/Timezone' } })
  fireEvent.click(screen.getByRole('button', { name: 'Review calendar day' }))
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Unknown calendar timezone'))
  cleanup()
})
