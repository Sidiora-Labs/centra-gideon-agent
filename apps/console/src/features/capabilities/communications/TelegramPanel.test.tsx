import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { TelegramPanel } from './TelegramPanel'

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

it('previews an operational command from real people and saves explicit Telegram settings', async () => {
  const response = await originalFetch(origin + base + '/people', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'Telegram UI Friend' }) })
  expect(response.status).toBe(201)
  render(<TelegramPanel />)
  await screen.findByText('No Telegram delivery attempts.')
  fireEvent.click(screen.getByRole('button', { name: 'Preview Telegram command' }))
  await screen.findByText('Telegram UI Friend: missing')
  expect(screen.getByLabelText('Enable Telegram delivery')).not.toBeChecked()
  fireEvent.change(screen.getByLabelText('Bot credential reference'), { target: { value: 'ABSENT_UI_TELEGRAM_BOT_734A' } })
  fireEvent.change(screen.getByLabelText('Webhook secret reference'), { target: { value: 'ABSENT_UI_TELEGRAM_HOOK_734A' } })
  fireEvent.change(screen.getByLabelText('Allowed Telegram chat IDs'), { target: { value: '12345' } })
  fireEvent.change(screen.getByLabelText('Allowed Telegram user IDs'), { target: { value: '12345' } })
  fireEvent.click(screen.getByLabelText('Enable Telegram delivery'))
  fireEvent.click(screen.getByRole('button', { name: 'Save Telegram settings' }))
  await screen.findByText('Telegram settings saved')
  expect(screen.getByLabelText('Automatically reply to authorized operational commands')).not.toBeChecked()
  const saved = await originalFetch(origin + base + '/telegram/config')
  const settings = (await saved.json()).config
  expect(settings.enabled).toBe(true)
  expect(settings.automatic_replies).toBe(false)
  expect(settings.allowed_chat_ids).toEqual([12345])
  expect(settings.allowed_user_ids).toEqual([12345])
  expect(settings.revision).toBe(1)
  cleanup()
})

it('queues a real notification and keeps it queued when credentials are unavailable', async () => {
  render(<TelegramPanel />)
  await waitFor(() => expect(screen.getByLabelText('Bot credential reference')).toHaveValue('ABSENT_UI_TELEGRAM_BOT_734A'))
  fireEvent.change(screen.getByLabelText('Notification Telegram chat'), { target: { value: '12345' } })
  fireEvent.change(screen.getByLabelText('Telegram notification text'), { target: { value: 'Reviewed Telegram notice' } })
  fireEvent.click(screen.getByRole('button', { name: 'Queue Telegram notification' }))
  await screen.findByText('Chat 12345: queued')
  expect(screen.getByLabelText('Telegram notification text')).toHaveValue('')
  expect(location.hash).toContain('telegram_chat=12345')
  fireEvent.click(screen.getByRole('button', { name: 'Send queued Telegram notification' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('bot credential is unavailable')
  expect(screen.getByText('Chat 12345: queued')).toBeInTheDocument()
  const response = await originalFetch(origin + base + '/telegram/deliveries')
  const rows = (await response.json()).deliveries
  expect(rows).toHaveLength(1)
  expect(rows[0].message_id).toBeNull()
  expect(rows[0].text).toBe('Reviewed Telegram notice')
  cleanup()
})

it('restores attempts and refuses an unapproved chat without losing the composed text', async () => {
  render(<TelegramPanel />)
  await screen.findByText('Chat 12345: queued')
  expect(screen.getByLabelText('Notification Telegram chat')).toHaveValue('12345')
  fireEvent.change(screen.getByLabelText('Notification Telegram chat'), { target: { value: '777' } })
  fireEvent.change(screen.getByLabelText('Telegram notification text'), { target: { value: 'Must remain unsent' } })
  fireEvent.click(screen.getByRole('button', { name: 'Queue Telegram notification' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('not allowed')
  expect(screen.getByLabelText('Telegram notification text')).toHaveValue('Must remain unsent')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh Telegram attempts' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh Telegram attempts' })).not.toBeDisabled())
  expect(screen.getAllByText('Chat 12345: queued')).toHaveLength(1)
  fireEvent.change(screen.getByLabelText('Operational command'), { target: { value: '/people' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview Telegram command' }))
  await screen.findByText('Telegram UI Friend (tribe)')
  cleanup()
})
