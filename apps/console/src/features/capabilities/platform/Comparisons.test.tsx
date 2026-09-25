import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import Comparisons from './Comparisons'
let server: ChildProcess
let baseUrl: string
let home: string
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-comparisons-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/comparisons_ui_server.py'], {
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
function fill(model: string, source = 'https://example.org/report') {
  const values = { model, corpus: 'same-corpus-v1', metric: 'accuracy-percent', score: '75', source, observed_at: '2026-09-01', methodology: 'Imported test observation, no local model execution.' }
  for (const [field, value] of Object.entries(values)) fireEvent.change(screen.getByLabelText(`Observation ${field}`), { target: { value } })
}
it('persists two attributed models under same comparison group and removes records', async () => {
  const rendered = render(<Comparisons baseUrl={baseUrl} />)
  expect(await screen.findByText('No imported observations.')).toBeVisible()
  expect(screen.getByText('No recorded judge benchmarks.')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Add imported observation' })).toBeDisabled()
  fill('model-a')
  fireEvent.click(screen.getByRole('button', { name: 'Add imported observation' }))
  expect(await screen.findByRole('button', { name: 'Remove model-a' })).toBeVisible()
  fill('model-b')
  fireEvent.click(screen.getByRole('button', { name: 'Add imported observation' }))
  expect(await screen.findByRole('button', { name: 'Remove model-b' })).toBeVisible()
  expect(screen.getAllByRole('table')).toHaveLength(1)
  expect(screen.getByRole('heading', { name: 'same-corpus-v1 · accuracy-percent' })).toBeVisible()
  expect(screen.getAllByRole('link', { name: 'Imported source' })).toHaveLength(2)
  const data = await (await fetch(`${baseUrl}/api/capabilities/platform/comparisons`)).json()
  expect(data.imports.map((row: { origin: string }) => row.origin)).toEqual(['imported', 'imported'])
  rendered.unmount()
  render(<Comparisons baseUrl={baseUrl} />)
  await screen.findByRole('button', { name: 'Remove model-a' })
  fireEvent.click(screen.getByRole('button', { name: 'Remove model-a' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove model-a' })).toBeNull())
  fireEvent.click(screen.getByRole('button', { name: 'Remove model-b' }))
  expect(await screen.findByText('No imported observations.')).toBeVisible()
})
it('retains invalid source and displays the real request error', async () => {
  render(<Comparisons baseUrl={baseUrl} />)
  await screen.findByText('No imported observations.')
  fill('rejected-model', 'file:///etc/passwd')
  fireEvent.click(screen.getByRole('button', { name: 'Add imported observation' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('HTTP link')
  expect(screen.getByLabelText('Observation source')).toHaveValue('file:///etc/passwd')
  expect(screen.queryByRole('button', { name: 'Remove rejected-model' })).toBeNull()
})
