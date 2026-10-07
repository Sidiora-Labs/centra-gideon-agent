import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import VoiceControls from './VoiceControls'

const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'voice-controls-lifetime-'))
let child: ChildProcess
let origin: string
let base: string
let claims = 0
let invoker: HTMLButtonElement | undefined

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python',
    ['checks/runtime/capabilities/experience/audio_release_ui_server.py'], {
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
  base = origin + '/api/capabilities/experience'
  expect((await originalFetch(base + '/speech-owner')).status).toBe(403)
  globalThis.fetch = async (input, init) => {
    const target = typeof input === 'string' && input.startsWith('/') ? origin + input : input
    const url = target instanceof Request ? target.url : String(target)
    const headers = new Headers(target instanceof Request ? target.headers : undefined)
    new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
    if (new URL(url).origin === origin) headers.set('Authorization', `Bearer ${ready.token}`)
    const response = await originalFetch(target, { ...init, headers })
    if (url === base + '/speech-owner/claim' && response.ok) claims++
    return response
  }
}, 20000)

afterEach(() => { cleanup(); invoker?.remove(); invoker = undefined })
afterAll(async () => {
  globalThis.fetch = originalFetch
  if (child?.exitCode === null && child.signalCode === null) {
    const stopped = new Promise<void>(done => child.once('exit', () => done()))
    child.kill('SIGKILL')
    await stopped
  }
  rmSync(home, { recursive: true, force: true })
})

it('keeps the actual proactive runtime and audible lease mounted when voice controls close', async () => {
  invoker = document.createElement('button')
  invoker.textContent = 'Open voice controls'
  document.body.append(invoker)
  invoker.focus()
  const onClose = vi.fn()
  const props = { items: [], navigate: () => {}, currentRoute: 'dashboard', baseUrl: base, onClose }
  const mounted = render(<VoiceControls {...props} open />)
  const panel = screen.getByRole('region', { name: 'Proactive speech' })
  const close = screen.getByRole('button', { name: 'Close voice controls' })
  await waitFor(() => expect(close).toHaveFocus())
  expect(screen.getByRole('dialog', { name: 'Voice controls' })).toHaveAttribute('aria-modal', 'true')
  fireEvent.click(screen.getByRole('button', { name: 'Enable proactive speech here' }))
  await waitFor(async () => {
    expect(claims).toBe(1)
    expect((await (await fetch(origin + '/api/test/pending')).json()).pending).toBe(true)
  })
  const owner = (await (await fetch(base + '/speech-owner')).json()).owner.owner
  expect(owner).toMatch(/^[a-f0-9]+$/)

  mounted.rerender(<VoiceControls {...props} open={false} />)
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(panel.isConnected).toBe(true)
  expect(invoker).toHaveFocus()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(onClose).not.toHaveBeenCalled()
  expect((await (await fetch(base + '/speech-owner')).json()).owner.owner).toBe(owner)

  mounted.rerender(<VoiceControls {...props} open />)
  expect(screen.getByRole('region', { name: 'Proactive speech' })).toBe(panel)
  expect(screen.getByRole('button', { name: 'Disable proactive speech' })).toBeInTheDocument()
  expect(claims).toBe(1)
  expect((await (await fetch(base + '/speech-owner')).json()).owner.owner).toBe(owner)
  fireEvent.click(screen.getByRole('button', { name: 'Close voice controls' }))
  expect(onClose).toHaveBeenCalledTimes(1)

  mounted.unmount()
  await waitFor(async () => expect((await (await fetch(base + '/speech-owner')).json()).owner.owner).toBe(''))
})
