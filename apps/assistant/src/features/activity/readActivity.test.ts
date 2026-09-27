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
import React from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { ActivityController, useActivity } from '/src/features/activity/useActivity.ts'
import { nextActivityOffset } from '/src/features/activity/readActivity.ts'
import { ownerScope, signInOwner } from '/src/shared/auth.web.tsx'
window.activity = new ActivityController()
window.nextActivityOffset = nextActivityOffset
window.signInOwner = signInOwner
window.ownerScope = ownerScope
window.hookObservations = []
const hookRoot = createRoot(document.createElement('div'))
function CaptureActivity({ scope }) {
  const view = useActivity(scope)
  window.hookView = view
  window.hookObservations.push({ requested: scope?.cacheKey ?? null,
    owner: view.snapshot.ownerScopeKey, entries: view.snapshot.entries.length })
  return null
}
window.renderActivityHook = scope => flushSync(() => hookRoot.render(React.createElement(CaptureActivity, { scope })))
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
    expect(await evaluate('window.nextActivityOffset(0, 20, 20)')).toBeNull()
    expect(await evaluate('window.nextActivityOffset(0, 20, null)')).toBe(20)

    await new Promise(done => setTimeout(done, 1100))
    await control(server.control, '/task', { title: 'Inserted between pages' })
    await evaluate(`window.activity.loadMore('task')`)
    const second = await evaluate('window.activity.getSnapshot().sources.task')
    expect(second.entries).toHaveLength(22)
    expect(new Set(second.entries.map((entry: { identity: { key: string } }) => entry.identity.key)).size).toBe(22)
    expect(first.sources.task.entries.every((entry: { identity: { key: string } }) =>
      second.entries.some((next: { identity: { key: string } }) => next.identity.key === entry.identity.key))).toBe(true)
    expect(second.nextOffset).toBeNull()

    await control(server.control, '/trigger-pages', {})
    await evaluate('window.activity.refresh()')
    const triggerPage = await evaluate('window.activity.getSnapshot().sources.trigger_run')
    expect(triggerPage.entries).toHaveLength(19)
    expect(triggerPage.omittedWithoutId).toBe(1)
    expect(triggerPage.nextOffset).toBe(20)
    await evaluate(`window.activity.loadMore('trigger_run')`)
    const triggerTail = await evaluate('window.activity.getSnapshot().sources.trigger_run')
    expect(triggerTail.entries).toHaveLength(20)
    expect(triggerTail.omittedWithoutId).toBe(1)
    expect(triggerTail.coverage).toBe('missing_ids')
    expect(triggerTail.entries.some((entry: { identity: { sourceId: string } }) =>
      entry.identity.sourceId === 'trigger-1')).toBe(true)

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

    await control(server.control, '/empty-inbox', {})
    await evaluate('window.activity.refresh()')
    const emptyInbox = await evaluate('window.activity.getSnapshot().sources.inbox_item')
    expect(emptyInbox.phase).toBe('empty')
    expect(emptyInbox.freshness).toBe('current')
    expect(emptyInbox.entries).toHaveLength(0)
    expect(emptyInbox.error).toBeNull()
    expect(await evaluate('window.activity.getSnapshot().sources.task.entries.length')).toBe(20)

    await evaluate('window.renderActivityHook(window.scopeA)')
    await evaluate('window.hookView.refresh()')
    expect(await evaluate('window.hookView.snapshot.entries.length')).toBeGreaterThan(0)
    const sameOwner = await evaluate(`(() => {
      window.hookObservations = []
      window.renderActivityHook({ ...window.scopeA })
      return window.hookObservations
    })()`)
    expect(sameOwner.every((render: { owner: string; entries: number }) =>
      render.owner === first.sources.task.entries[0].identity.ownerScopeKey && render.entries > 0)).toBe(true)
    const switchedRender = await evaluate(`(() => {
      window.hookObservations = []
      window.scopeB = window.ownerScope(location.origin, { user: 'owner-b' })
      window.renderActivityHook(window.scopeB)
      return window.hookObservations
    })()`)
    expect(switchedRender.length).toBeGreaterThan(0)
    expect(switchedRender.every((render: { requested: string; owner: string; entries: number }) =>
      render.requested === render.owner && render.entries === 0)).toBe(true)

    await control(server.control, '/delay', { seconds: 0.4 })
    await evaluate(`(() => { window.pendingRefresh = window.activity.refresh();
      window.activity.setScope(window.scopeB);
      return window.activity.getSnapshot().entries.length })()`)
    expect(await evaluate('window.activity.getSnapshot().entries.length')).toBe(0)
    await evaluate('window.pendingRefresh')
    expect(await evaluate('window.activity.getSnapshot().entries.length')).toBe(0)
    expect(await evaluate('window.activity.getSnapshot().ownerScopeKey')).toBe(switchedRender[0].requested)

    await control(server.control, '/delay', { seconds: 0 })
    await evaluate('window.activity.setScope(window.scopeA)')
    await evaluate('window.activity.refresh()')
    await control(server.control, '/workflow-store', { unavailable: true })
    await control(server.control, '/delay', { seconds: 0.4, path: '/api/workflows/runs' })
    const failuresBefore = await (await fetch(`${server.control}/failures`)).json() as { workflow: number }
    await evaluate(`(() => {
      window.pendingFailure = window.activity.refresh()
      window.activity.setScope(window.scopeB)
      return true
    })()`)
    await evaluate('window.pendingFailure')
    const failuresAfter = await (await fetch(`${server.control}/failures`)).json() as { workflow: number }
    expect(failuresAfter.workflow).toBeGreaterThan(failuresBefore.workflow)
    const afterLateFailure = await evaluate(`(() => {
      const snapshot = window.activity.getSnapshot()
      return { owner: snapshot.ownerScopeKey, entries: snapshot.entries.length,
        workflow: snapshot.sources.workflow_run }
    })()`)
    expect(afterLateFailure.owner).toBe(switchedRender[0].requested)
    expect(afterLateFailure.entries).toBe(0)
    expect(afterLateFailure.workflow.phase).toBe('idle')
    expect(afterLateFailure.workflow.error).toBeNull()
    expect(afterLateFailure.workflow.entries).toHaveLength(0)
  }, 30000)
})
