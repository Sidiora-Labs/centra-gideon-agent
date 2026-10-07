import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { api } from '../../shared/data/api'
import { SemanticFactEditor } from './MemoryPanel'

const root = process.env.GIDEON_TEST_ROOT || resolve(process.cwd(), '../..')
const nativeFetch = globalThis.fetch
let home: string
let server: ChildProcess
let origin: string
let ownerToken: string
let sessionKey: string
let temporarySessionKey: string
const requests: { method: string; savedValue: string | null }[] = []

const pythonServer = `import asyncio
import json
import signal
import time
from aiohttp import web
from gideon.core.config import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.handlers.memory import api_memory_semantic, api_memory_semantic_write
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware, validate_token
from gideon.security.approval_answer import OWNER, Principal, principal_record
from gideon.security.session_credentials import begin_turn, end_turn
async def main():
    token = generate_token('memory-editor-owner')
    valid, owner, reason = validate_token(token, use_session_exp=True)
    assert valid, reason
    principal = Principal(OWNER, owner)
    state = ConsoleState(ConversationDirectory(AppConfig()), time.time())
    session = state.get_or_create_session('memory-editor', memory_mode='persistent')
    session._initiator = principal_record(principal)
    temporary = state.get_or_create_session('memory-editor-temporary', memory_mode='temporary')
    temporary._initiator = principal_record(principal)
    session_key = 'dashboard:' + session.key
    credential = begin_turn(session_key, principal, turn_id='memory-editor-fixture', memory_mode=session.memory_mode)
    assert credential is not None
    app = web.Application(middlewares=[token_auth_middleware()])
    app['state'] = state
    app.router.add_get('/api/memory/semantic', api_memory_semantic)
    app.router.add_put('/api/memory/semantic', api_memory_semantic_write)
    runner = web.AppRunner(app)
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    try:
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        print(json.dumps({'port': site._server.sockets[0].getsockname()[1], 'owner_token': token, 'session_key': session_key, 'temporary_session_key': 'dashboard:' + temporary.key}), flush=True)
        await stop.wait()
    finally:
        await runner.cleanup()
        end_turn(credential)
asyncio.run(main())`

beforeAll(async () => {
  home = await mkdtemp(resolve(tmpdir(), 'gideon-memory-fact-ui-'))
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', pythonServer], {
    cwd: root,
    env: { ...process.env, GIDEON_HOME: home, GIDEON_WORKSPACE: resolve(home, 'workspace'), PYTHONPATH: resolve(root, 'runtime') },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  origin = await new Promise<string>((accept, reject) => {
    let errors = ''
    server.stderr?.on('data', (chunk) => { errors += String(chunk) })
    server.once('error', reject)
    server.once('exit', (code) => reject(new Error(`Memory HTTP server exited ${code}: ${errors}`)))
    let pending = ''
    server.stdout?.on('data', (chunk) => {
      pending += String(chunk)
      const lines = pending.split('\n')
      pending = lines.pop() ?? ''
      for (const line of lines) {
        if (!line.startsWith('{')) continue
        try {
          const ready = JSON.parse(line) as { port: number; owner_token: string; session_key: string; temporary_session_key: string }
          if (!Number.isInteger(ready.port) || !ready.owner_token || !ready.session_key || !ready.temporary_session_key) continue
          ownerToken = ready.owner_token
          sessionKey = ready.session_key
          temporarySessionKey = ready.temporary_session_key
          accept(`http://127.0.0.1:${ready.port}`)
        } catch (error) { reject(error) }
      }
    })
  })
  globalThis.fetch = async (input, init) => {
    const headers = new Headers(init?.headers)
    headers.set('Authorization', `Bearer ${ownerToken}`)
    headers.set('X-Session-Key', sessionKey)
    const response = await nativeFetch(typeof input === 'string' && input.startsWith('/') ? origin + input : input, { ...init, headers })
    requests.push({ method: init?.method || 'GET', savedValue: screen.queryByLabelText('Saved fact value')?.textContent ?? null })
    return response
  }
})

afterEach(() => cleanup())
afterAll(async () => {
  globalThis.fetch = nativeFetch
  if (server && server.exitCode === null) {
    const stopped = new Promise<void>((done) => server.once('exit', () => done()))
    server.kill('SIGTERM')
    await stopped
  }
  if (home) await rm(home, { recursive: true, force: true })
})

describe('semantic fact inspector editing', () => {
  it('refuses unsigned requests and owner writes from a temporary session', async () => {
    const body = JSON.stringify({ key: 'pref.editor.denied', value: 'Must not persist' })
    const unsigned = await nativeFetch(`${origin}/api/memory/semantic`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Session-Key': sessionKey }, body,
    })
    expect(unsigned.status).toBe(403)
    expect(unsigned.headers.get('X-Auth-Required')).toBe('true')
    const temporary = await nativeFetch(`${origin}/api/memory/semantic`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${ownerToken}`, 'X-Session-Key': temporarySessionKey }, body,
    })
    expect(temporary.status).toBe(403)
    expect(await temporary.json()).toMatchObject({ error: 'Memory writes are not allowed in this session mode.' })
    expect((await api.memorySemantic()).find(entry => entry.key === 'pref.editor.denied')).toBeUndefined()
  })

  it('saves an edited fact under its existing key', async () => {
    const key = 'pref.editor.timezone'
    await api.writeSemantic(key, 'Europe/Berlin')
    const fact = (await api.memorySemantic()).find((entry) => entry.key === key)
    expect(fact).toBeDefined()
    let saved = 0
    render(<SemanticFactEditor fact={fact!} onSaved={() => { saved += 1 }} />)
    expect(screen.getByLabelText('Saved fact value')).toHaveTextContent('Europe/Berlin')
    fireEvent.click(screen.getByRole('button', { name: 'Edit fact' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Fact value' }), { target: { value: 'Asia/Tokyo' } })
    expect(screen.getByLabelText('Saved fact value')).toHaveTextContent('Europe/Berlin')
    requests.length = 0
    fireEvent.click(screen.getByRole('button', { name: 'Save fact' }))
    await screen.findByRole('status')
    expect(requests).toEqual([
      { method: 'PUT', savedValue: 'Europe/Berlin' },
      { method: 'GET', savedValue: 'Europe/Berlin' },
    ])
    expect(screen.getByLabelText('Saved fact value')).toHaveTextContent('Asia/Tokyo')
    expect(saved).toBe(1)
    const stored = await api.memorySemantic()
    expect(stored).toHaveLength(1)
    expect(stored[0].key).toBe(key)
    expect(JSON.parse(stored[0].value_json!)).toBe('Asia/Tokyo')
  })
})
