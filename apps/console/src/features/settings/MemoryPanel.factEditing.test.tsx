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
const requests: { method: string; savedValue: string | null }[] = []

const pythonServer = `import asyncio
import time
from aiohttp import web
from gideon.core.config import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.handlers.memory import api_memory_semantic, api_memory_semantic_write
async def main():
    app = web.Application()
    app['state'] = ConsoleState(ConversationDirectory(AppConfig()), time.time())
    app.router.add_get('/api/memory/semantic', api_memory_semantic)
    app.router.add_put('/api/memory/semantic', api_memory_semantic_write)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    print(site._server.sockets[0].getsockname()[1], flush=True)
    await asyncio.Event().wait()
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
    server.stdout?.on('data', (chunk) => {
      const port = String(chunk).trim().split('\n').find((line) => /^\d+$/.test(line))
      if (port) accept(`http://127.0.0.1:${port}`)
    })
  })
  globalThis.fetch = async (input, init) => {
    const response = await nativeFetch(typeof input === 'string' && input.startsWith('/') ? origin + input : input, init)
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
