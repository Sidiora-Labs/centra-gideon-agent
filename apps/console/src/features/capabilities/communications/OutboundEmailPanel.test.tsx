import { afterAll, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { OutboundEmailPanel } from './OutboundEmailPanel'

let server: ChildProcess, origin: string, home: string
const originalFetch = globalThis.fetch

beforeAll(async () => {
  home = mkdtempSync(resolve(tmpdir(), 'gideon-outbound-ui-'))
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/communications/outbound_email_ui_server.py'], { cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  origin = await new Promise<string>((resolveOrigin, reject) => {
    let output = '', errors = ''
    server.stderr?.on('data', chunk => { errors += String(chunk) })
    server.stdout?.on('data', chunk => { output += String(chunk); const line = output.split('\n').find(v => v.startsWith('{"port":')); if (line) resolveOrigin(`http://127.0.0.1:${JSON.parse(line).port}`) })
    server.on('error', reject); server.on('exit', code => reject(new Error(`server exited ${code}: ${errors}`)))
  })
  globalThis.fetch = (input, init) => originalFetch(typeof input === 'string' && input.startsWith('/') ? origin + input : input, init)
})

afterAll(() => { cleanup(); globalThis.fetch = originalFetch; server?.kill(); rmSync(home, { recursive: true, force: true }) })

it('requires exact approval before real SMTP dispatch and reports acceptance as delivery uncertain', async () => {
  render(<OutboundEmailPanel />)
  await screen.findByRole('option', { name: 'Customer mail — owner@example.com' })
  expect(screen.getByText(/fixed sender and credential/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Recipients'), { target: { value: 'privacyrequest@whitepages.com' } })
  fireEvent.change(screen.getByLabelText('Subject'), { target: { value: 'Privacy opt-out request' } })
  fireEvent.change(screen.getByLabelText('Body'), { target: { value: 'Please remove my exact Whitepages profile.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create exact draft' }))
  await screen.findByText('From: owner@example.com')
  expect(screen.getByText('To: privacyrequest@whitepages.com')).toBeInTheDocument()
  expect(screen.getAllByText('Please remove my exact Whitepages profile.')).toHaveLength(2)
  expect(screen.queryByRole('button', { name: 'Send approved email' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Approve exact email' })).toBeDisabled()
  fireEvent.click(screen.getByLabelText('I approve these exact recipients and body'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Approve exact email' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Approve exact email' }))
  await screen.findByLabelText('Dispatch this approved email')
  expect(screen.getByRole('button', { name: 'Send approved email' })).toBeDisabled()
  const before = await originalFetch(origin + '/contract/messages').then(r => r.json())
  expect(before.messages).toHaveLength(0)
  fireEvent.click(screen.getByLabelText('Dispatch this approved email'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Send approved email' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Send approved email' }))
  await screen.findByText(/provider acceptance: accepted; delivery: uncertain/)
  const contract = await originalFetch(origin + '/contract/messages').then(r => r.json())
  expect(contract.messages).toHaveLength(1)
  expect(contract.messages[0].sender).toBe('owner@example.com')
  expect(contract.messages[0].recipients).toEqual(['privacyrequest@whitepages.com'])
  expect(contract.messages[0].data).toContain('Please remove my exact Whitepages profile.')
})

it('correlates a canonical reply and renders an untrusted link as unopened', async () => {
  cleanup(); render(<OutboundEmailPanel />)
  const option = await screen.findByRole('option', { name: /Privacy opt-out request — accepted/ })
  fireEvent.change(screen.getByLabelText('Durable draft'), { target: { value: (option as HTMLOptionElement).value } })
  const selected = (option as HTMLOptionElement).value
  await originalFetch(origin + '/contract/reply', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ draft_id: selected }) })
  fireEvent.click(screen.getByRole('button', { name: 'Check ingested verification replies' }))
  await screen.findByText(/Verification inbox reference: <ui-reply@whitepages.com>/)
  expect(screen.getByText(/Unopened link: https:\/\/untrusted.example\/verify \(not opened\)/)).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /untrusted/ })).not.toBeInTheDocument()
})

it('reloads the accepted receipt without presenting a duplicate send control', async () => {
  cleanup(); render(<OutboundEmailPanel />)
  const option = await screen.findByRole('option', { name: /Privacy opt-out request — verified/ })
  fireEvent.change(screen.getByLabelText('Durable draft'), { target: { value: (option as HTMLOptionElement).value } })
  await screen.findByText(/provider acceptance: accepted; delivery: uncertain/)
  expect(screen.queryByRole('button', { name: 'Send approved email' })).not.toBeInTheDocument()
  const contract = await originalFetch(origin + '/contract/messages').then(r => r.json())
  expect(contract.messages).toHaveLength(1)
})
