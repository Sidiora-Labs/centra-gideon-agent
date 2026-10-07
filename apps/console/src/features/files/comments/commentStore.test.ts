import { describe, expect, it, beforeAll, afterAll } from 'vitest'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import type { DocComment } from './commentStore'

describe('server-backed comment store', () => {
  const pending: Promise<Response>[] = []
  const writes: Array<{ method: string; body: string; status: number; response: unknown }> = []
  const originalFetch = globalThis.fetch
  const home = mkdtempSync(join(tmpdir(), 'doc-id-console-'))
  let child: ChildProcess
  beforeAll(async () => {
    const root = resolve(process.cwd(), '../..')
    child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', ['-u', '-c', `
import asyncio, json
from aiohttp import web
from gideon.interfaces.dashboard.handlers.doc_comments import setup_doc_comment_routes
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
async def main():
    app = web.Application(middlewares=[token_auth_middleware()])
    setup_doc_comment_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    listener = web.TCPSite(runner, '127.0.0.1', 0)
    await listener.start()
    print(json.dumps({'port': listener._server.sockets[0].getsockname()[1], 'token': generate_token('doc-id-test-owner')}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
asyncio.run(main())
`], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'] })
    let errors = ''
    child.stderr?.on('data', chunk => { errors += String(chunk) })
    const ready = await new Promise<{ port: number; token: string }>((done, fail) => {
      let output = ''
      const timeout = setTimeout(() => fail(new Error(errors || 'Document comment server startup timed out')), 20000)
      child.once('exit', code => { clearTimeout(timeout); fail(new Error(`Document comment server exited ${code}: ${errors}`)) })
      child.stdout?.on('data', chunk => {
        output += String(chunk)
        const line = output.split('\n').find(value => value.startsWith('{"port":'))
        if (line) { clearTimeout(timeout); done(JSON.parse(line)) }
      })
    })
    const origin = `http://127.0.0.1:${ready.port}`
    const refused = await originalFetch(origin + '/api/doc-comments')
    expect(refused.status).toBe(403)
    globalThis.fetch = (input, init) => {
      const target = typeof input === 'string' && input.startsWith('/') ? origin + input : input
      const url = target instanceof Request ? target.url : String(target)
      const headers = new Headers(target instanceof Request ? target.headers : undefined)
      new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
      if (new URL(url).origin === origin) headers.set('Authorization', `Bearer ${ready.token}`)
      const operation = originalFetch(target, { ...init, headers }).then(async response => {
        if (new URL(url).origin === origin && init?.method && init.method !== 'GET') writes.push({ method: init.method, body: String(init.body ?? ''), status: response.status, response: await response.clone().json() })
        return response
      })
      pending.push(operation)
      return operation
    }
  }, 25000)
  afterAll(async () => {
    await Promise.allSettled(pending)
    globalThis.fetch = originalFetch
    if (child && child.exitCode === null && child.signalCode === null) {
      await new Promise<void>(done => {
        const timeout = setTimeout(() => { child.kill('SIGKILL'); done() }, 5000)
        child.once('exit', () => { clearTimeout(timeout); done() })
        child.kill('SIGTERM')
      })
    }
    rmSync(home, { recursive: true, force: true })
  })

  it('uses persisted server IDs, converts seconds to milliseconds, and sends the real bulk envelope', async () => {
    localStorage.clear()
    const { commentStore } = await import('./commentStore')
    await Promise.all(pending)
    await commentStore.resync()
    expect(commentStore.error()).toBeUndefined()
    expect(commentStore.all()).toEqual([])
    await commentStore.add({ docId: 'd', docLabel: 'D', quote: 'q', comment: 'c' })
    expect(commentStore.error()).toBeUndefined()
    const stored: DocComment[] = JSON.parse(readFileSync(join(home, 'doc_comments.json'), 'utf8'))
    expect(stored).toHaveLength(1)
    expect(stored[0].id).toMatch(/^c-[0-9a-f]{32}$/)
    expect(stored[0].ts).toBeGreaterThan(0)
    expect(commentStore.all()).toEqual([{ ...stored[0], ts: stored[0].ts * 1000 }])
    expect(writes[0]).toMatchObject({ method: 'POST', status: 201, response: { comment: stored[0] } })
    await commentStore.resync()
    expect(commentStore.all()).toEqual([{ ...stored[0], ts: stored[0].ts * 1000 }])
    await commentStore.removeMany([stored[0].id])
    expect(commentStore.error()).toBeUndefined()
    const deleted = writes.at(-1)!
    expect(deleted).toMatchObject({ method: 'DELETE', status: 200, response: { deleted: 1 } })
    expect(JSON.parse(deleted.body)).toEqual({ ids: [stored[0].id] })
    expect(JSON.parse(readFileSync(join(home, 'doc_comments.json'), 'utf8'))).toEqual([])
    await commentStore.resync()
    expect(commentStore.all()).toEqual([])
    expect(localStorage.length).toBe(0)
  })
})
