import { describe, expect, it, beforeEach, beforeAll, afterAll } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { commentStore } from '../files/comments/commentStore'

// global localStorage key (`doc-comments-v1`) shared by every surface that can be commented on:

const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the comment store keys documents by docId alone', () => {
  const originalFetch = globalThis.fetch
  const home = mkdtempSync(join(tmpdir(), 'doc-id-console-'))
  let child: ChildProcess
  beforeAll(async () => {
    const root = resolve(process.cwd(), '../..')
    child = spawn(process.env.GIDEON_TEST_PYTHON || resolve(root, '.venv/bin/python'), ['-u', '-c', `
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
      return originalFetch(target, { ...init, headers })
    }
  }, 25000)
  afterAll(async () => {
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

  beforeEach(async () => { await commentStore.resync(); await commentStore.clear() })

  it('two documents with the SAME id are one document — the defect, reproduced', async () => {
    await commentStore.add({ docId: 'code-plan-requirements', docLabel: 'requirements plan', quote: 'q', comment: 'from project A' })
    expect(commentStore.error()).toBeUndefined()
    const forProjectB = commentStore.all().filter((c) => c.docId === 'code-plan-requirements')
    expect(forProjectB).toHaveLength(1)
    expect(forProjectB[0].comment).toBe('from project A')
  })

  it('project-scoped ids keep two projects apart', async () => {
    await commentStore.add({ docId: 'code-plan-projA-requirements', docLabel: 'requirements plan', quote: 'q', comment: 'from A' })
    await commentStore.add({ docId: 'code-plan-projB-requirements', docLabel: 'requirements plan', quote: 'q', comment: 'from B' })
    expect(commentStore.error()).toBeUndefined()
    const a = commentStore.all().filter((c) => c.docId === 'code-plan-projA-requirements')
    const b = commentStore.all().filter((c) => c.docId === 'code-plan-projB-requirements')
    expect(a).toHaveLength(1)
    expect(b).toHaveLength(1)
    expect(a[0].comment).toBe('from A')
    expect(b[0].comment).toBe('from B')
  })
})

describe('both planning views scope their artifact docId to their own run', () => {
  it('CodePlanningView includes the projectId', () => {
    expect(read('features/code/CodePlanningView.tsx')).toMatch(/docId=\{`code-plan-\$\{projectId\}-\$\{kind\}`\}/)
  })

  it('LoopPlanningView includes the loopId (unchanged, pinned)', () => {
    expect(read('features/loops/LoopPlanningView.tsx')).toMatch(/docId=\{`plan-\$\{loopId\}-\$\{kind\}`\}/)
  })

  it('the Code config is a factory, which is what lets the renderer reach the id', () => {
    const src = read('features/code/CodePlanningView.tsx')
    expect(src).toMatch(/function makeCfg\(projectId: string\): WalkthroughConfig/)
    expect(/^const CFG: WalkthroughConfig/m.test(src), 'CFG should no longer be a module constant').toBe(false)
    expect(src).toMatch(/cfg=\{makeCfg\(projectId\)\}/)
  })

  it('no planning docId is built from the step kind alone', () => {
    for (const rel of ['features/code/CodePlanningView.tsx', 'features/loops/LoopPlanningView.tsx']) {
      const code = read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      const ids = [...code.matchAll(/docId=\{`([^`]+)`\}/g)].map((m) => m[1])
      expect(ids.length, `${rel} should build at least one docId`).toBeGreaterThan(0)
      for (const id of ids) {
        expect(
          /\$\{(projectId|loopId)\}/.test(id),
          `${rel}: docId \`${id}\` has no run scope — two runs would share one comment thread`,
        ).toBe(true)
      }
    }
  })
})
