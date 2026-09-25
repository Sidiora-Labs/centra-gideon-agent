import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { HashRouter } from 'react-router-dom'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import Labs from './Labs'

let server: ChildProcess
let origin: string
let home: string
const networkFetch = globalThis.fetch
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-labs-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_labs import register
async def main():
 app = web.Application()
 register(app, Path(os.environ['GIDEON_HOME']))
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

const content = 'analyte,observed_at,value,unit,reference_low,reference_high,external_id,notes\nGlucose,2026-09-25T09:00:00+02:00,92,mg/dL,70,100,first,original result\nGlucose,2026-09-26T09:00:00+02:00,95,mg/dL,70,100,second,second result'

test('CSV preview, commit, correction, original reference and chronological trend use real HTTP', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing/labs')
  const view = render(<HashRouter><Labs /></HashRouter>)
  await screen.findByText('No laboratory records.')
  fireEvent.change(screen.getByLabelText('Filename'), { target: { value: 'lab.csv' } })
  fireEvent.change(screen.getByLabelText('Laboratory source'), { target: { value: 'uploaded report' } })
  fireEvent.change(screen.getByLabelText('Source content'), { target: { value: content } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview import' }))
  await screen.findByText('2 rows; 0 duplicates.')
  const empty = await (await networkFetch(`${origin}/api/capabilities/wellbeing/labs`)).json()
  expect(empty.records).toEqual([])
  fireEvent.click(screen.getByRole('button', { name: 'Commit import' }))
  await screen.findByText('Imported 2; duplicates 0.')
  const button = await screen.findByRole('button', { name: /Glucose: 92 mg\/dL/ })
  fireEvent.click(button)
  await screen.findByText('Laboratory correction history')
  expect(screen.getByText('Revision 1: 92 mg/dL · original result')).toBeInTheDocument()
  expect(screen.getByLabelText('Reference low')).toHaveValue('70')
  expect(screen.getByLabelText('Reference high')).toHaveValue('100')
  const attachment = screen.getByRole('link', { name: 'Original attachment · version 1' })
  expect(attachment.getAttribute('href')).toMatch(/^\/api\/capabilities\/wellbeing\/labs\/[a-f0-9]+\/source$/)
  const sourceFile = await networkFetch(new URL(attachment.getAttribute('href')!, origin))
  expect(await sourceFile.text()).toBe(content)
  expect(screen.getByText('Recorded trend · mg/dL')).toBeInTheDocument()
  expect(screen.getByText('2026-09-25T09:00:00+02:00: 92 mg/dL')).toBeInTheDocument()
  expect(screen.getByText('2026-09-26T09:00:00+02:00: 95 mg/dL')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Result value'), { target: { value: '93' } })
  fireEvent.change(screen.getByLabelText('Correction notes'), { target: { value: 'transcription corrected' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save laboratory correction' }))
  await screen.findByText('Revision 2: 93 mg/dL · transcription corrected')
  expect(screen.getByText('Revision 1: 92 mg/dL · original result')).toBeInTheDocument()
  expect(location.hash).toContain('?id=')
  const id = new URLSearchParams(location.hash.split('?')[1]).get('id')
  const saved = await (await networkFetch(`${origin}/api/capabilities/wellbeing/labs/${id}`)).json()
  expect(saved.value).toBe(93)
  expect(saved.source).toBe('uploaded report')
  expect(saved.reference_high).toBe(100)
  view.unmount()
  render(<HashRouter><Labs /></HashRouter>)
  await screen.findByText('Revision 2: 93 mg/dL · transcription corrected')
  expect(screen.getByLabelText('Result value')).toHaveValue('93')
  fireEvent.change(screen.getByLabelText('Reference low'), { target: { value: '120' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save laboratory correction' }))
  expect((await screen.findByRole('alert')).textContent).toContain('reference_low')
  const history = await (await networkFetch(`${origin}/api/capabilities/wellbeing/labs/${id}/history`)).json()
  expect(history.history).toHaveLength(2)
  expect(history.history[0].value).toBe(92)
  expect(history.history[1].value).toBe(93)
})

test('changed input hides stale preview and duplicate-safe commit reports actual counts', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing/labs')
  render(<HashRouter><Labs /></HashRouter>)
  await waitFor(() => expect(screen.queryByText('Loading laboratory records…')).not.toBeInTheDocument())
  fireEvent.change(screen.getByLabelText('Filename'), { target: { value: 'copied.csv' } })
  fireEvent.change(screen.getByLabelText('Laboratory source'), { target: { value: 'uploaded report' } })
  fireEvent.change(screen.getByLabelText('Source content'), { target: { value: content } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview import' }))
  await screen.findByText('2 rows; 2 duplicates.')
  fireEvent.change(screen.getByLabelText('Filename'), { target: { value: 'renamed.csv' } })
  expect(screen.queryByRole('button', { name: 'Commit import' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Preview import' }))
  await screen.findByRole('button', { name: 'Commit import' })
  fireEvent.click(screen.getByRole('button', { name: 'Commit import' }))
  await screen.findByText('Imported 0; duplicates 2.')
  const result = await (await networkFetch(`${origin}/api/capabilities/wellbeing/labs`)).json()
  expect(result.records).toHaveLength(2)
  fireEvent.change(screen.getByLabelText('Filter analyte'), { target: { value: 'Unknown analyte' } })
  fireEvent.click(screen.getByRole('button', { name: 'Filter laboratory records' }))
  await screen.findByText('No laboratory records.')
  fireEvent.change(screen.getByLabelText('Import format'), { target: { value: 'json' } })
  fireEvent.change(screen.getByLabelText('Source content'), { target: { value: 'not-json' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview import' }))
  expect((await screen.findByRole('alert')).textContent).toContain('Expecting value')
  expect(screen.queryByRole('button', { name: 'Commit import' })).not.toBeInTheDocument()
})
