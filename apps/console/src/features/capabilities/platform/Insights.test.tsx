import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import Insights from './Insights'
let server: ChildProcess
let baseUrl: string
let home: string
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-insights-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/insights_ui_server.py'], {
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
it('saves and revises actual source-linked artifact and reads historical versions', async () => {
  render(<Insights baseUrl={baseUrl} />)
  expect(await screen.findByText('body_weight')).toBeVisible()
  expect(screen.getByText('{"weight":-1} kg')).toBeVisible()
  expect(screen.getByText(/Changes do not establish causes/)).toBeVisible()
  fireEvent.change(screen.getByLabelText('Narrative note'), { target: { value: 'First interpretation' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save scorecard version' }))
  expect(await screen.findByText('Saved version 1')).toBeVisible()
  const first = await (await fetch(`${baseUrl}/api/capabilities/platform/insights`)).json()
  const slug = first.narratives[0].slug
  const original = JSON.parse(await readFile(`${home}/artifacts/${slug}/versions/v1.html`, 'utf8'))
  expect(original.note).toBe('First interpretation')
  expect(original.snapshot.rows[0].observations).toHaveLength(2)
  expect(original.snapshot.rows[0].observations[0].source).toBe('scale')
  fireEvent.change(screen.getByLabelText('Narrative note'), { target: { value: 'Revised interpretation' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save scorecard version' }))
  expect(await screen.findByText('Saved version 2')).toBeVisible()
  const revision = JSON.parse(await readFile(`${home}/artifacts/${slug}/versions/v2.html`, 'utf8'))
  expect(revision.note).toBe('Revised interpretation')
  expect(revision.snapshot.fingerprint).toBe(original.snapshot.fingerprint)
  fireEvent.change(screen.getByLabelText('Narrative version'), { target: { value: '1' } })
  await waitFor(() => expect(screen.getByLabelText('Narrative note')).toHaveValue('First interpretation'))
  expect(screen.getByLabelText('Saved source snapshot')).toHaveTextContent('scale')
  expect(JSON.parse(await readFile(`${home}/artifacts/${slug}/versions/v1.html`, 'utf8')).note).toBe('First interpretation')
})
it('surfaces concurrent version conflict from the real server without overwriting', async () => {
  render(<Insights baseUrl={baseUrl} />)
  await screen.findByText('body_weight')
  fireEvent.click(await screen.findByRole('button', { name: 'Personal scorecard · v2' }))
  await screen.findByText('Saved version 2')
  const state = await (await fetch(`${baseUrl}/api/capabilities/platform/insights`)).json()
  const response = await fetch(`${baseUrl}/api/capabilities/platform/insights`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fingerprint: state.fingerprint, note: 'Concurrent writer', slug: state.narratives[0].slug, version: 2 }) })
  expect(response.status).toBe(200)
  fireEvent.change(screen.getByLabelText('Narrative note'), { target: { value: 'Stale editor' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save scorecard version' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/Narrative changed/)
  const current = await (await fetch(`${baseUrl}/api/capabilities/platform/insights?slug=${state.narratives[0].slug}`)).json()
  expect(current.version).toBe(3)
  expect(current.document.note).toBe('Concurrent writer')
  expect(current.versions).toEqual([1, 2, 3])
})
