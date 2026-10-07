import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import OwnedAudio from './OwnedAudio'
import ProactiveSpeech from './ProactiveSpeech'
import { claimAudible, releaseAudible, type AudibleLease } from './audibleOwner'
import { speechPlaybackActive } from './speechPlayback'

const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'audio-release-'))
const notices: Array<{ message: string; level: string }> = []
const onNotice = (event: Event) => notices.push((event as CustomEvent).detail)
let child: ChildProcess
let origin: string
let base: string
let claimed: AudibleLease | undefined
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', ['checks/runtime/capabilities/experience/audio_release_ui_server.py'], {
    cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  let errors = ''
  child.stderr!.on('data', chunk => { errors += String(chunk) })
  const ready = await new Promise<{ port: number; token: string }>((done, fail) => {
    let buffer = ''
    const timeout = setTimeout(() => fail(new Error(errors || 'Audio HTTP startup timed out')), 15000)
    child.on('exit', code => { clearTimeout(timeout); fail(new Error(`Audio HTTP exited ${code}: ${errors}`)) })
    child.stdout!.on('data', chunk => {
      buffer += String(chunk)
      for (const line of buffer.split('\n')) if (line.startsWith('{"port":')) {
        try { const parsed = JSON.parse(line); clearTimeout(timeout); done(parsed) } catch { /* complete line required */ }
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
    if (url === base + '/speech-owner/claim' && response.ok) claimed = (await response.clone().json()).owner
    return response
  }
  window.addEventListener('ne:toast', onNotice)
}, 20000)
afterEach(() => { cleanup(); notices.length = 0; claimed = undefined })
afterAll(async () => {
  window.removeEventListener('ne:toast', onNotice)
  globalThis.fetch = originalFetch
  if (child?.exitCode === null && child.signalCode === null) {
    const stopped = new Promise<void>(done => child.once('exit', () => done()))
    child.kill('SIGKILL')
    await stopped
  }
  rmSync(home, { recursive: true, force: true })
})

async function replaceLease() {
  await waitFor(() => expect(claimed).toBeDefined())
  await releaseAudible(base, claimed!)
  return claimAudible(base)
}
async function expectReleaseFailure() {
  await waitFor(() => expect(notices.some(n => n.level === 'error' && n.message.includes('Speech stopped locally, but audible ownership could not be released'))).toBe(true))
  expect(speechPlaybackActive()).toBe(false)
}

it('stops local speech and reports a real rejected release without revoking a replacement owner', async () => {
  render(<OwnedAudio src="data:audio/wav;base64," baseUrl={base} />)
  fireEvent.click(screen.getByRole('button', { name: 'Play speech' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Pause speech' })).toBeInTheDocument())
  fireEvent.playing(screen.getByLabelText('Scene narration audio'))
  expect(speechPlaybackActive()).toBe(true)
  const replacement = await replaceLease()
  fireEvent.click(screen.getByRole('button', { name: 'Pause speech' }))
  await expectReleaseFailure()
  expect(screen.getByRole('button', { name: 'Play speech' })).toBeInTheDocument()
  expect((await (await fetch(base + '/speech-owner')).json()).owner.owner).toBe(replacement.owner)
  await releaseAudible(base, replacement)
})

it('reports real OwnedAudio cleanup refusal after unmount without active playback', async () => {
  const mounted = render(<OwnedAudio src="data:audio/wav;base64," baseUrl={base} />)
  fireEvent.click(screen.getByRole('button', { name: 'Play speech' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Pause speech' })).toBeInTheDocument())
  const replacement = await replaceLease()
  mounted.unmount()
  await expectReleaseFailure()
  expect((await (await fetch(base + '/speech-owner')).json()).owner.owner).toBe(replacement.owner)
  await releaseAudible(base, replacement)
})

it('reports proactive cleanup connection failure when its real native request is still in flight', async () => {
  const mounted = render(<ProactiveSpeech baseUrl={base} />)
  fireEvent.click(screen.getByRole('button', { name: 'Enable proactive speech here' }))
  await waitFor(async () => expect((await (await fetch(origin + '/api/test/pending')).json()).pending).toBe(true))
  expect(screen.queryByLabelText('Proactive digest audio')).not.toBeInTheDocument()
  const stopped = new Promise<void>(done => child.once('exit', () => done()))
  child.kill('SIGKILL')
  await stopped
  mounted.unmount()
  await expectReleaseFailure()
})
