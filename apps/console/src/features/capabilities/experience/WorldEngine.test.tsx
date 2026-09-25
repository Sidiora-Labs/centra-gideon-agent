import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import WorldEngine from './WorldEngine'
let child: ChildProcess
let home: string
let base: string
const root = resolve(process.cwd(), '../..')
beforeAll(async () => {
  home = await mkdtemp(resolve(tmpdir(), 'gideon-navigation-ui-'))
  child = spawn('/tmp/gideon-runtime-venv/bin/python', ['checks/runtime/capabilities/experience/serve_ui.py', home], { cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  base = await new Promise<string>((accept, reject) => {
    let output = '', errors = ''
    child.stdout!.on('data', data => { output += String(data); if (output.includes('\n')) accept(output.trim() + '/api/capabilities/experience') })
    child.stderr!.on('data', data => { errors += String(data) })
    child.on('exit', code => reject(new Error(`HTTP process exited ${code}: ${errors}`)))
    child.on('error', reject)
  })
})
afterAll(async () => { child?.kill(); await rm(home, { recursive: true, force: true }) })
it('displays actual missing engine readiness and never exposes an unavailable iframe', async () => {
  render(<WorldEngine baseUrl={base} />)
  await screen.findByRole('status')
  expect(screen.getByRole('status')).toHaveTextContent('unavailable')
  expect(screen.getByRole('button', { name: 'Start world engine' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Stop world engine' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Open world' })).toBeDisabled()
  expect(screen.queryByTitle('Persistent world')).not.toBeInTheDocument()
  const status = await (await fetch(base + '/world-engine')).json()
  expect(status.state).toBe('unavailable')
  expect(status.version).toBeNull()
  expect(status.engine_url).toBeNull()
  expect(screen.getByRole('link', { name: 'Eidoverse Worlds' })).toHaveAttribute('href', 'https://github.com/atomantic/eidoverse-worlds')
  expect(screen.getByText(/AGPL-3.0/)).toBeVisible()
})
it('refreshes authoritative status and keeps operator configuration outside customer controls', async () => {
  render(<WorldEngine baseUrl={base} />)
  await screen.findByRole('status')
  const before = await (await fetch(base + '/world-engine')).json()
  fireEvent.click(screen.getByRole('button', { name: 'Refresh world engine' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(before.reason))
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  const response = await fetch(base + '/world-engine/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target: 'http://other.invalid' }) })
  expect(response.status).toBe(400)
  const after = await (await fetch(base + '/world-engine')).json()
  expect(after).toEqual(before)
  expect(screen.queryByTitle('Persistent world')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Open world' })).toBeDisabled()
})
it('does not convert a successful unavailable start response into running UI', async () => {
  const response = await fetch(base + '/world-engine/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
  expect(response.status).toBe(200)
  const status = await response.json()
  expect(status.state).toBe('unavailable')
  render(<WorldEngine baseUrl={base} />)
  await screen.findByRole('status')
  expect(screen.getByRole('status')).toHaveTextContent(status.reason)
  expect(screen.queryByText(/Actual managed world engine answered/)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Stop world engine' })).toBeDisabled()
  expect(screen.queryByTitle('Persistent world')).not.toBeInTheDocument()
  const proxy = await fetch(base + '/world-engine/host/version')
  expect(proxy.status).toBe(409)
})
