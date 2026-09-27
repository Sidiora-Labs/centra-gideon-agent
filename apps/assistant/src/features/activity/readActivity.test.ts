import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer, type ViteDevServer } from 'vite'
import { afterAll, describe, expect, it } from 'vitest'

const root = resolve(process.cwd(), '../..')
const children: ChildProcessWithoutNullStreams[] = []
const directories: string[] = []
let vite: ViteDevServer | undefined
let debuggerSocket: WebSocket | undefined

afterAll(async () => {
  debuggerSocket?.close()
  for (const child of children) child.kill('SIGTERM')
  if (vite) await vite.close()
  for (const directory of directories) await rm(directory, { recursive: true, force: true })
})

async function port(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  const address = server.address()
  const value = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(done => server.close(() => done()))
  return value
}

async function nativeServer(origin: string): Promise<{ api: string; control: string }> {
  const child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3',
    [join(root, 'apps/assistant/test-support/activity_server.py'), origin],
    { env: { ...process.env } })
  children.push(child)
  const line = await new Promise<string>((done, reject) => {
    let output = ''
    let errors = ''
    const timeout = setTimeout(() => reject(new Error(`Activity server timed out: ${errors}`)), 15000)
    child.stdout.on('data', chunk => {
      output += String(chunk)
      if (output.includes('\n')) { clearTimeout(timeout); done(output.split('\n')[0]) }
    })
    child.stderr.on('data', chunk => { errors += String(chunk) })
    child.once('exit', code => { clearTimeout(timeout); reject(new Error(`Activity server exited ${code}: ${errors}`)) })
  })
  const ports = JSON.parse(line) as { api_port: number; control_port: number }
  return { api: `http://127.0.0.1:${ports.api_port}`, control: `http://127.0.0.1:${ports.control_port}` }
}

async function browser(address: string): Promise<(expression: string) => Promise<any>> {
  const directory = await mkdtemp(join(tmpdir(), 'gideon-activity-browser-'))
  directories.push(directory)
  const debuggingPort = await port()
  const child = spawn(process.env.CHROMIUM_BIN || 'chromium', [
    '--headless', '--no-sandbox', '--disable-gpu', '--disable-background-networking',
    `--remote-debugging-port=${debuggingPort}`, `--user-data-dir=${directory}`, 'about:blank',
  ])
  children.push(child)
  let target: { webSocketDebuggerUrl: string } | undefined
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${debuggingPort}/json`)).json() as
        Array<{ type: string; webSocketDebuggerUrl: string }>
      target = targets.find(item => item.type === 'page')
      if (target) break
    } catch { await new Promise(done => setTimeout(done, 100)) }
    if (!target) await new Promise(done => setTimeout(done, 100))
  }
  if (!target) throw new Error('Chromium did not start')
  debuggerSocket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise<void>((done, reject) => {
    debuggerSocket!.addEventListener('open', () => done(), { once: true })
    debuggerSocket!.addEventListener('error', () => reject(new Error('Chromium debugger failed')), { once: true })
  })
  let nextId = 0
  const pending = new Map<number, { done: (value: any) => void; reject: (error: Error) => void }>()
  debuggerSocket.addEventListener('message', event => {
    const response = JSON.parse(String(event.data)) as { id?: number; result?: any; error?: { message: string } }
    if (!response.id) return
    const request = pending.get(response.id)
    if (!request) return
    pending.delete(response.id)
    if (response.error) request.reject(new Error(response.error.message))
    else request.done(response.result)
  })
  const command = (method: string, params: Record<string, unknown> = {}) => new Promise<any>((done, reject) => {
    const id = ++nextId
    pending.set(id, { done, reject })
    debuggerSocket!.send(JSON.stringify({ id, method, params }))
  })
  await command('Page.navigate', { url: address })
  return async (expression: string) => {
    const response = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text)
    return response.result?.value
  }
}

async function control(address: string, route: string, body: object): Promise<void> {
  const response = await fetch(`${address}${route}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) })
  expect(response.status).toBe(200)
}

describe('canonical Gideon Activity reads', () => {
  it('pages native records and isolates source failure, stale recovery, and owner changes', async () => {
    const webPort = await port()
    const origin = `http://127.0.0.1:${webPort}`
    const server = await nativeServer(origin)
    vite = await createServer({
      configFile: false, root: join(root, 'apps/assistant'),
      plugins: [{
        name: 'activity-integration',
        resolveId(id) { if (id === '/activity-entry.ts') return '\0activity-entry' },
        load(id) { if (id === '\0activity-entry') return `
import { ActivityController } from '/src/features/activity/useActivity.ts'
import { ownerScope, signInOwner } from '/src/shared/auth.web.tsx'
window.activity = new ActivityController()
window.signInOwner = signInOwner
window.ownerScope = ownerScope
window.loaded = true
` },
        configureServer(devServer) {
          devServer.middlewares.use('/integration', (_request, response) => {
            response.setHeader('Content-Type', 'text/html; charset=utf-8')
            response.end('<!doctype html><html><body><script type="module" src="/activity-entry.ts"></script></body></html>')
          })
        },
      }],
      server: { host: '127.0.0.1', port: webPort, strictPort: true, proxy: { '/api': server.api } },
    })
    await vite.listen()
    const evaluate = await browser(`${origin}/integration`)
    for (let attempt = 0; attempt < 100 && !(await evaluate('window.loaded === true')); attempt++) {
      await new Promise(done => setTimeout(done, 50))
    }
    expect(await evaluate('window.loaded === true')).toBe(true)
    await evaluate(`(async () => { const owner = await window.signInOwner('owner-a', 'correct-horse-battery-staple');
      window.scopeA = window.ownerScope(location.origin, owner); window.activity.setScope(window.scopeA);
      await window.activity.refresh(); return true })()`)
    const first = await evaluate(`(() => { const snapshot = window.activity.getSnapshot();
      return { sources: snapshot.sources, ids: snapshot.entries.map(e => e.identity.key) } })()`)
    expect(first.sources.task.entries).toHaveLength(20)
    expect(first.sources.task.nextOffset).toBe(20)
    expect(first.sources.workflow_run.entries[0].identity.sourceId).toBe('workflow-1')
    expect(first.sources.trigger_run.entries[0].identity.sourceId).toBe('trigger-1')
    expect(first.sources.inbox_item.entries[0].related.approvalId).toBe('approval-1')
    expect(first.sources.approval.entries[0].identity.sourceId).toBe('approval-1')
    expect(first.sources.artifact.entries[0].identity.sourceId).toBe('native-result')
    expect(first.sources.notification.coverage).toBe('missing_ids')
    expect(first.sources.notification.omittedWithoutId).toBe(1)
    expect(first.sources.chat_session.phase).toBe('unavailable')
    await evaluate(`window.activity.loadMore('task')`)
    const second = await evaluate('window.activity.getSnapshot().sources.task')
    expect(second.entries).toHaveLength(22)
    expect(second.nextOffset).toBeNull()

    await control(server.control, '/workflow-store', { unavailable: true })
    await evaluate('window.activity.refresh()')
    const stale = await evaluate(`(() => { const snapshot = window.activity.getSnapshot();
      return { workflow: snapshot.sources.workflow_run, tasks: snapshot.sources.task } })()`)
    expect(stale.workflow.phase).toBe('failed')
    expect(stale.workflow.freshness).toBe('stale')
    expect(stale.workflow.entries[0].identity.sourceId).toBe('workflow-1')
    expect(stale.tasks.phase).toBe('ready')
    await control(server.control, '/workflow-store', { unavailable: false })
    await control(server.control, '/task', { title: 'Recovered task' })
    await evaluate('window.activity.returnedFromDetail()')
    const recovered = await evaluate(`(() => { const snapshot = window.activity.getSnapshot();
      return { workflow: snapshot.sources.workflow_run, task: snapshot.sources.task } })()`)
    expect(recovered.workflow.freshness).toBe('current')
    expect(recovered.workflow.phase).toBe('ready')
    expect(recovered.task.nextOffset).toBe(20)

    await control(server.control, '/delay', { seconds: 0.4 })
    await evaluate(`(() => { window.pendingRefresh = window.activity.refresh();
      window.activity.setScope({ ...window.scopeA, ownerId: 'owner-b', cacheKey: '["owner-b"]' });
      return window.activity.getSnapshot().entries.length })()`)
    expect(await evaluate('window.activity.getSnapshot().entries.length')).toBe(0)
    await evaluate('window.pendingRefresh')
    expect(await evaluate('window.activity.getSnapshot().entries.length')).toBe(0)
    expect(await evaluate('window.activity.getSnapshot().ownerScopeKey')).toBe('["owner-b"]')
  }, 30000)
})
