import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api, channelTrustResponse } from '../../shared/data/api'
import { SETTINGS_WIDGETS } from './settingsWidgets'
const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'settings-trust-contract-'))
let child: ChildProcess
let origin: string
let base: string

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python',
    ['checks/runtime/channel_trust_ui_server.py'], {
      cwd: root,
      env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
  let errors = ''
  child.stderr!.on('data', data => { errors += String(data) })
  const ready = await new Promise<{ port: number; token: string }>((done, fail) => {
    let output = ''
    const timeout = setTimeout(() => fail(new Error(errors || 'Voice HTTP startup timed out')), 15000)
    child.on('error', error => { clearTimeout(timeout); fail(error) })
    child.on('exit', code => { clearTimeout(timeout); fail(new Error(`Voice HTTP exited ${code}: ${errors}`)) })
    child.stdout!.on('data', data => {
      output += String(data)
      for (const line of output.split('\n')) {
        if (!line.startsWith('{"port":')) continue
        try { const value = JSON.parse(line); clearTimeout(timeout); done(value) } catch { /* Wait for the complete line. */ }
      }
    })
  })
  origin = `http://127.0.0.1:${ready.port}`
  base = origin + '/api/channels/trust'
  expect((await originalFetch(base)).status).toBe(403)
  globalThis.fetch = async (input, init) => {
    const target = typeof input === 'string' && input.startsWith('/') ? origin + input : input
    const url = target instanceof Request ? target.url : String(target)
    const headers = new Headers(target instanceof Request ? target.headers : undefined)
    new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
    if (new URL(url).origin === origin) headers.set('Authorization', `Bearer ${ready.token}`)
    const response = await originalFetch(target, { ...init, headers })
    return response
  }
}, 20000)

afterEach(() => { cleanup() })
afterAll(async () => {
  globalThis.fetch = originalFetch
  if (child?.exitCode === null && child.signalCode === null) {
    const stopped = new Promise<void>(done => child.once('exit', () => done()))
    child.kill('SIGKILL')
    await stopped
  }
  rmSync(home, { recursive: true, force: true })
})


it('renders actual authenticated native sender trust in Settings search and navigation', async () => {
  const native = await (await fetch(base)).json()
  expect(native.providers).toBeUndefined()
  const trust = await api.channelTrust()
  expect(trust.providers.find(row => row.provider === 'telegram')?.allowed_senders[0].name).toBe('Trusted operator')
  const widget = SETTINGS_WIDGETS.find(item => item.id === 'sender-trust')!
  const go = vi.fn()
  function Card() {
    const search = widget.useSearchText()
    return <><output aria-label="Searchable trust">{search}</output>{widget.render('', go)}</>
  }
  render(<Card />)
  await waitFor(() => expect(screen.getByLabelText('Searchable trust')).toHaveTextContent('Trusted operator'))
  expect(screen.getByText('trusted sender')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Open Sender trust settings' }))
  expect(go).toHaveBeenCalledWith('sender-trust')
})
it('rejects invalid trust responses while allowing a genuine empty directory', () => {
  expect(() => channelTrustResponse({})).toThrow('Invalid channel trust response')
  expect(() => channelTrustResponse({ trust_rows: [{}], dm_policies: [], group_policies: [], default_dm_policy: 'pairing', default_group_policy: 'tracked_only' })).toThrow('Invalid channel trust provider response')
  expect(channelTrustResponse({ trust_rows: [], dm_policies: [], group_policies: [], default_dm_policy: 'pairing', default_group_policy: 'tracked_only' }).providers).toEqual([])
})
