import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import Accounting from './Accounting'
let server: ChildProcess
let baseUrl: string
let home: string
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-accounting-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/accounting_ui_server.py'], {
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
it('shows historical recorded credential binding usage and unknown live billing', async () => {
  render(<Accounting baseUrl={baseUrl} />)
  expect(await screen.findByText('Work API')).toBeVisible()
  expect(screen.getByText('work-account')).toBeVisible()
  expect(screen.getByText('150 / 30')).toBeVisible()
  expect(screen.getByText('$0.250000')).toBeVisible()
  expect(screen.getByText(/2 recorded turns/)).toBeVisible()
  expect(screen.getByText(/vendor plan, billing and live quotas are unavailable/)).toBeVisible()
  const value = await (await fetch(`${baseUrl}/api/capabilities/platform/accounting`)).json()
  expect(value.rows[0].instance_id).toBe((await readFile(`${home}/machine_id`, 'utf8')).trim())
  expect(value.rows[0].billed_cost_usd).toBeNull()
  expect(value.rows[0].quota_remaining).toBeNull()
  expect(value.rows[0].unpriced_turns).toBe(1)
})
it('filters actual retained source window and refreshes without rewriting ledger', async () => {
  render(<Accounting baseUrl={baseUrl} />)
  await screen.findByText('150 / 30')
  const before = await readFile(`${home}/usage/turns.jsonl`, 'utf8')
  fireEvent.change(screen.getByLabelText('Accounting window'), { target: { value: '1' } })
  expect(await screen.findByText('100 / 20')).toBeVisible()
  expect(screen.getByText(/1 recorded turns/)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Refresh accounting' }))
  await waitFor(() => expect(screen.getByText('100 / 20')).toBeVisible())
  expect(await readFile(`${home}/usage/turns.jsonl`, 'utf8')).toBe(before)
  const result = await (await fetch(`${baseUrl}/api/capabilities/platform/accounting?days=1`)).json()
  expect(result.turns).toBe(1)
  expect(result.rows[0].unpriced_turns).toBe(0)
  expect(result.rows[0].credential_ref).toBe('work-account')
})
it('imports an actual Claude Code JSONL file and reports unknown pricing without duplicating it', async () => {
  render(<Accounting baseUrl={baseUrl} />)
  await screen.findByText('150 / 30')
  const line = JSON.stringify({ type: 'assistant', sessionId: 'ui-claude-session', timestamp: new Date().toISOString(), message: { id: 'ui-message-1', model: 'claude-sonnet-4-5', usage: { input_tokens: 70, output_tokens: 12, cache_read_input_tokens: 8, cache_creation_input_tokens: 2 } } })
  const file = new File([line + '\n'], 'claude-ui.jsonl', { type: 'application/x-ndjson' })
  fireEvent.change(screen.getByLabelText('CLI usage history file'), { target: { files: [file] } })
  fireEvent.click(screen.getByRole('button', { name: 'Import usage history' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Imported 1 records · 0 duplicates · 0 invalid lines · pricing unknown')
  expect(await screen.findByText(/3 recorded turns · 1 imported/)).toBeVisible()
  expect(screen.getByText('70 / 12')).toBeVisible()
  expect(screen.getByText('1 (claude_code_jsonl)')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Import usage history' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Imported 0 records · 1 duplicates'))
  const result = await (await fetch(`${baseUrl}/api/capabilities/platform/accounting`)).json()
  expect(result.imported_turns).toBe(1)
  expect(result.rows.find((row: { provider_instance: string }) => row.provider_instance === 'claude-cli-import').recorded_cost_usd).toBe(0)
})
