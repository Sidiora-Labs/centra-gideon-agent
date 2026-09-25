import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import Substances from './Substances'
import { HashRouter } from 'react-router-dom'

let server: ChildProcess
let origin: string
let home: string
const networkFetch = globalThis.fetch
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-substances-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_substances import register
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

const base = '/api/capabilities/wellbeing/substances'
const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })
const open = () => { window.location.hash = ''; return render(<HashRouter><Substances /></HashRouter>) }

test('real entries preserve correction history and delete through the gateway', async () => {
  open()
  await screen.findByText('No consumption entries.')
  change('Product name', 'Recorded beer')
  change('Volume per serving (mL)', '330')
  change('ABV (%)', '5')
  change('Servings or units', '2')
  change('Observed at (with offset)', new Date().toISOString())
  change('Entry source', 'manual')
  change('Entry notes', 'with lunch')
  fireEvent.click(screen.getByRole('button', { name: 'Save consumption entry' }))
  const button = await screen.findByRole('button', { name: /Recorded beer: 26.037 g ethanol/ })
  expect(screen.getByText('Ethanol: 26.037 g · 1 logged days')).toBeInTheDocument()
  expect(screen.getByText('Labeled nicotine: Not recorded mg · 0 logged days')).toBeInTheDocument()
  fireEvent.click(button)
  await screen.findByRole('heading', { name: 'Edit consumption entry' })
  expect(screen.getByLabelText('Entry source')).toBeDisabled()
  expect(screen.getByLabelText('Entry notes')).toHaveValue('with lunch')
  change('Servings or units', '1')
  change('Entry notes', 'corrected serving')
  fireEvent.click(screen.getByRole('button', { name: 'Save consumption entry' }))
  await screen.findByText(/Revision 2: Recorded beer · 1 units/)
  expect(screen.getByText(/Revision 1: Recorded beer · 2 units/)).toBeInTheDocument()
  expect(screen.getByText('Ethanol: 13.019 g · 1 logged days')).toBeInTheDocument()
  const entries = await (await networkFetch(origin + base + '/entries')).json()
  expect(entries.entries).toHaveLength(1)
  const identity = entries.entries[0].id
  expect(entries.entries[0].revision).toBe(2)
  expect(entries.entries[0].notes).toBe('corrected serving')
  fireEvent.click(screen.getByRole('button', { name: 'Delete consumption entry' }))
  await screen.findByText('No consumption entries.')
  expect(screen.getByText('Ethanol: Not recorded g · 0 logged days')).toBeInTheDocument()
  const history = await (await networkFetch(origin + base + '/entries/' + identity + '/history')).json()
  expect(history.history).toHaveLength(3)
  expect(history.history[2].deleted).toBe(true)
  expect(history.history[0].count).toBe(2)
  change('Summary timezone', 'Not/AZone')
  fireEvent.click(screen.getByRole('button', { name: 'Apply summary timezone' }))
  expect((await screen.findByRole('alert')).textContent).toContain('timezone')
})

test('product presets snapshot labeled zero nicotine and survive preset changes', async () => {
  open()
  await screen.findByText('No consumption entries.')
  fireEvent.click(screen.getByRole('button', { name: 'Manage product presets' }))
  await screen.findByRole('heading', { name: 'New product preset' })
  change('Preset kind', 'nicotine')
  change('Product name', 'Zero product')
  change('Labeled nicotine per unit (mg)', '0')
  fireEvent.click(screen.getByRole('button', { name: 'Save product preset' }))
  await screen.findByRole('button', { name: 'Zero product · nicotine' })
  fireEvent.click(screen.getByRole('button', { name: 'New entry' }))
  await screen.findByLabelText('Product preset')
  const products = await (await networkFetch(origin + base + '/presets')).json()
  expect(products.presets).toHaveLength(1)
  change('Product preset', products.presets[0].id)
  expect(screen.queryByLabelText('Product name')).not.toBeInTheDocument()
  change('Servings or units', '2')
  change('Entry source', 'label')
  fireEvent.click(screen.getByRole('button', { name: 'Save consumption entry' }))
  await screen.findByRole('button', { name: /Zero product: 0 mg nicotine/ })
  expect(screen.getByText('Labeled nicotine: 0 mg · 1 logged days')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Manage product presets' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Zero product · nicotine' }))
  await screen.findByRole('heading', { name: 'Edit product preset' })
  change('Product name', 'Changed product')
  change('Labeled nicotine per unit (mg)', '4')
  fireEvent.click(screen.getByRole('button', { name: 'Save product preset' }))
  await screen.findByRole('button', { name: 'Changed product · nicotine' })
  fireEvent.click(screen.getByRole('button', { name: 'Delete product preset' }))
  await screen.findByText('No product presets.')
  fireEvent.click(screen.getByRole('button', { name: 'New entry' }))
  await screen.findByRole('button', { name: /Zero product: 0 mg nicotine/ })
  const result = await (await networkFetch(origin + base + '/entries')).json()
  expect(result.entries[0].preset_revision).toBe(1)
  expect(result.entries[0].details.mg_per_unit).toBe(0)
  expect(result.entries[0].nicotine_mg).toBe(0)
  expect(result.entries[0].source).toBe('label')
})
