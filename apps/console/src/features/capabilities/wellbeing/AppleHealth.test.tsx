import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import AppleHealth from './AppleHealth'

let server: ChildProcess
let origin: string
let home: string
const networkFetch = globalThis.fetch
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-apple-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_apple import register
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_labs import register as register_labs
async def main():
 app = web.Application()
 register(app, Path(os.environ['GIDEON_HOME']))
 register_labs(app, Path(os.environ['GIDEON_HOME']))
 runner = web.AppRunner(app)
 await runner.setup()
 site = web.TCPSite(runner, '127.0.0.1', 0)
 await site.start()
 print(site._server.sockets[0].getsockname()[1], flush=True)
 await asyncio.Event().wait()
asyncio.run(main())
`], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'runtime'), GIDEON_HOME: home } })
  origin = await new Promise<string>((resolveOrigin, reject) => {
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP server exited ${code}`)))
    server.stdout!.once('data', data => resolveOrigin(`http://127.0.0.1:${String(data).trim()}`))
    server.stderr!.on('data', data => { if (String(data).includes('Traceback')) reject(new Error(String(data))) })
  })
  globalThis.fetch = (input, init) => networkFetch(new URL(String(input), origin), init)
})
afterAll(async () => {
  cleanup()
  globalThis.fetch = networkFetch
  if (server?.exitCode === null) await new Promise<void>(resolveExit => { server.once('exit', () => resolveExit()); server.kill('SIGTERM') })
  rmSync(home, { recursive: true, force: true })
})

const xml = '<HealthData><Record type="HKQuantityTypeIdentifierStepCount" sourceName="Watch" unit="count" value="125" startDate="2026-09-25 09:00:00 +0200"/><Record type="Unsupported"/></HealthData>'

test('file selection, preview, commit and original download use the real importer', async () => {
  const view = render(<AppleHealth />)
  await screen.findByText('No imported metrics.')
  expect(screen.getByRole('button', { name: 'Preview export' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Choose export'), { target: { files: [new File([xml], 'export.xml', { type: 'text/xml' })] } })
  await screen.findByText('Selected: export.xml')
  fireEvent.change(screen.getByLabelText('Export source'), { target: { value: 'personal export' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview export' }))
  await screen.findByText('1 metrics; 0 laboratory results.')
  expect(screen.getByText('unsupported_xml_record: 1 skipped')).toBeInTheDocument()
  const empty = await (await networkFetch(`${origin}/api/capabilities/wellbeing/apple/metrics`)).json()
  expect(empty.metrics).toEqual([])
  fireEvent.click(screen.getByRole('button', { name: 'Commit export import' }))
  await screen.findByText('Imported 1 metrics and 0 lab results; 0 duplicates.')
  await screen.findByRole('heading', { name: 'HKQuantityTypeIdentifierStepCount: 125 count' })
  expect(screen.getByText('personal export · Watch')).toBeInTheDocument()
  const link = screen.getByRole('link', { name: 'Download original export' })
  const response = await networkFetch(new URL(link.getAttribute('href')!, origin))
  expect(response.status).toBe(200)
  expect(await response.text()).toBe(xml)
  expect(response.headers.get('Content-Disposition')).toContain('attachment')
  fireEvent.click(screen.getByRole('button', { name: 'Preview export' }))
  await screen.findByRole('button', { name: 'Commit export import' })
  fireEvent.click(screen.getByRole('button', { name: 'Commit export import' }))
  await screen.findByText('Imported 1 metrics and 0 lab results; 0 duplicates.')
  const replay = await (await networkFetch(`${origin}/api/capabilities/wellbeing/apple/metrics`)).json()
  expect(replay.metrics).toHaveLength(1)
  view.unmount()
  render(<AppleHealth />)
  await screen.findByRole('heading', { name: 'HKQuantityTypeIdentifierStepCount: 125 count' })
  fireEvent.change(screen.getByLabelText('Exact unit'), { target: { value: 'unknown' } })
  fireEvent.click(screen.getByRole('button', { name: 'Filter imported metrics' }))
  await screen.findByText('No imported metrics.')
  fireEvent.change(screen.getByLabelText('From timestamp with offset'), { target: { value: 'invalid' } })
  fireEvent.click(screen.getByRole('button', { name: 'Filter imported metrics' }))
  expect((await screen.findByRole('alert')).textContent).toContain('Timestamp')
})

test('FHIR upload feeds canonical laboratory records and malformed input leaves records intact', async () => {
  render(<AppleHealth />)
  await waitFor(() => expect(screen.queryByText('Loading imported metrics…')).not.toBeInTheDocument())
  const fhir = JSON.stringify({ resourceType: 'Observation', id: 'sample-ui', status: 'final', category: [{ coding: [{ code: 'laboratory' }] }], code: { text: 'Glucose' }, effectiveDateTime: '2026-09-25T09:00:00+02:00', valueQuantity: { value: 92, unit: 'mg/dL' }, referenceRange: [{ low: { value: 70 }, high: { value: 100 } }] })
  fireEvent.change(screen.getByLabelText('Choose export'), { target: { files: [new File([fhir], 'clinical.json', { type: 'application/json' })] } })
  await screen.findByText('Selected: clinical.json')
  fireEvent.change(screen.getByLabelText('Export source'), { target: { value: 'clinical export' } })
  fireEvent.change(screen.getByLabelText('Export format'), { target: { value: 'fhir' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview export' }))
  await screen.findByText('0 metrics; 1 laboratory results.')
  fireEvent.change(screen.getByLabelText('Export source'), { target: { value: 'changed clinical source' } })
  expect(screen.queryByRole('button', { name: 'Commit export import' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Preview export' }))
  await screen.findByRole('button', { name: 'Commit export import' })
  fireEvent.click(screen.getByRole('button', { name: 'Commit export import' }))
  await screen.findByText('Imported 0 metrics and 1 lab results; 0 duplicates.')
  const labs = await (await networkFetch(`${origin}/api/capabilities/wellbeing/labs`)).json()
  expect(labs.records).toHaveLength(1)
  expect(labs.records[0].value).toBe(92)
  expect(labs.records[0].reference_low).toBe(70)
  expect(labs.records[0].source).toBe('changed clinical source')
  const original = await networkFetch(`${origin}/api/capabilities/wellbeing/labs/${labs.records[0].id}/source`)
  expect(await original.text()).toBe(fhir)
  expect(screen.getByRole('link', { name: 'Open imported laboratory results' })).toHaveAttribute('href', '#/capabilities/wellbeing/labs')
  fireEvent.change(screen.getByLabelText('Choose export'), { target: { files: [new File(['<broken>'], 'broken.xml', { type: 'text/xml' })] } })
  await screen.findByText('Selected: broken.xml')
  fireEvent.click(screen.getByRole('button', { name: 'Preview export' }))
  expect((await screen.findByRole('alert')).textContent).toContain('Malformed Apple Health XML')
  expect(screen.queryByRole('button', { name: 'Commit export import' })).not.toBeInTheDocument()
  const unchanged = await (await networkFetch(`${origin}/api/capabilities/wellbeing/labs`)).json()
  expect(unchanged.records).toEqual(labs.records)
})
