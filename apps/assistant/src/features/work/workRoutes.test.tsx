import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer, type ViteDevServer } from 'vite'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import type { OwnerScope } from '../../shared/auth.web'
import { parseShellRoute, serializeShellRoute, type ShellReturnContext } from '../../shared/shell/shellRoutes'
import { GatewayError } from '../../shared/transport.web'
import WorkRoutes, { WORK_DESTINATIONS, createWorkRoute, findWorkDestination, workReturnRoute } from './WorkRoutes.web'
import { WorkClient, classifyWorkError, workEntry } from './workClient'
import { workModuleDefinitions } from './moduleDefinitions.web'

const scope: OwnerScope = Object.freeze({
  runtimeOrigin: 'http://127.0.0.1', ownerId: 'work-owner', cacheKey: '["work","work-owner"]',
})
const originalFetch = globalThis.fetch
let server: ChildProcessWithoutNullStreams | undefined
let api = ''
let taskId = ''
let projectId = ''
let vite: ViteDevServer | undefined
let debuggerSocket: WebSocket | undefined
const browserProcesses: ChildProcessWithoutNullStreams[] = []
const browserDirectories: string[] = []

async function port(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  const address = server.address()
  const value = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(done => server.close(() => done()))
  return value
}

async function browser(address: string): Promise<(expression: string) => Promise<unknown>> {
  const directory = await mkdtemp(join(tmpdir(), 'gideon-work-browser-'))
  browserDirectories.push(directory)
  const debuggingPort = await port()
  const child = spawn(process.env.CHROMIUM_BIN || 'chromium', [
    '--headless', '--no-sandbox', '--disable-gpu', '--disable-background-networking',
    `--remote-debugging-port=${debuggingPort}`, `--user-data-dir=${directory}`, 'about:blank',
  ])
  browserProcesses.push(child)
  let target: { webSocketDebuggerUrl: string } | undefined
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const targets = await (await originalFetch(`http://127.0.0.1:${debuggingPort}/json`)).json() as
        Array<{ type: string; webSocketDebuggerUrl: string }>
      target = targets.find(item => item.type === 'page')
      if (target) break
    } catch { await new Promise(done => setTimeout(done, 100)) }
  }
  if (!target) throw new Error('Chromium did not start')
  debuggerSocket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise<void>((done, reject) => {
    debuggerSocket!.addEventListener('open', () => done(), { once: true })
    debuggerSocket!.addEventListener('error', () => reject(new Error('Chromium debugger failed')), { once: true })
  })
  let nextId = 0
  const pending = new Map<number, { done: (value: unknown) => void; reject: (error: Error) => void }>()
  debuggerSocket.addEventListener('message', event => {
    const response = JSON.parse(String(event.data)) as { id?: number; result?: any; error?: { message: string } }
    if (!response.id) return
    const request = pending.get(response.id)
    if (!request) return
    pending.delete(response.id)
    if (response.error) request.reject(new Error(response.error.message))
    else request.done(response.result)
  })
  const command = (method: string, params: Record<string, unknown> = {}) => new Promise<unknown>((done, reject) => {
    const id = ++nextId
    pending.set(id, { done, reject })
    debuggerSocket!.send(JSON.stringify({ id, method, params }))
  })
  await command('Page.navigate', { url: address })
  return async expression => {
    const response = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }) as
      { result?: { value?: unknown }; exceptionDetails?: { text?: string; exception?: { description?: string } } }
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text)
    return response.result?.value
  }
}

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3',
    [join(root, 'apps/assistant/test-support/work_server.py')], {
      env: { ...process.env, PYTHONPATH: join(root, 'runtime') },
    })
  const line = await new Promise<string>((resolveLine, rejectLine) => {
    let output = ''
    let errors = ''
    const timeout = setTimeout(() => rejectLine(new Error(`Work server timed out: ${errors}`)), 15000)
    server!.stdout.on('data', chunk => {
      output += String(chunk)
      if (output.includes('\n')) {
        clearTimeout(timeout)
        resolveLine(output.split('\n')[0])
      }
    })
    server!.stderr.on('data', chunk => { errors += String(chunk) })
    server!.once('exit', code => {
      clearTimeout(timeout)
      rejectLine(new Error(`Work server exited ${code}: ${errors}`))
    })
  })
  const ready = JSON.parse(line) as { port: number; task_id: string; project_id: string }
  api = `http://127.0.0.1:${ready.port}`
  taskId = ready.task_id
  projectId = ready.project_id
  globalThis.fetch = (input, init) => originalFetch(new URL(String(input), api), init)
}, 20000)

afterAll(async () => {
  globalThis.fetch = originalFetch
  debuggerSocket?.close()
  for (const child of browserProcesses) child.kill('SIGTERM')
  if (vite) await vite.close()
  for (const directory of browserDirectories) await rm(directory, { recursive: true, force: true })
  server?.kill('SIGTERM')
})

describe('Work routes', () => {
  it('keeps every named family and Skills/Tools destination deep-linkable with source context', () => {
    const origin: ShellReturnContext = {
      destination: 'chat', sessionId: 'conversation-12', selectionId: 'message-8', scrollY: 340,
    }
    const unique = new Set<string>()
    for (const destination of WORK_DESTINATIONS) {
      const key = `${destination.id}:${destination.subview ?? ''}`
      expect(unique.has(key)).toBe(false)
      unique.add(key)
      const route = createWorkRoute(destination.id, 'canonical-record', origin, destination.subview)
      expect(findWorkDestination(route)).toEqual(destination)
      expect(parseShellRoute(serializeShellRoute(route))).toEqual(route)
      expect(route.record).toEqual({ kind: destination.kind, id: 'canonical-record' })
      expect(route.returnTo).toEqual(origin)
      expect(workModuleDefinitions.some(module => module.matches(route))).toBe(true)
    }
    expect(new Set(workModuleDefinitions.map(module => module.id)).size).toBe(workModuleDefinitions.length)
    expect(unique.has('skills:/edit')).toBe(true)
    expect(unique.has('skills:/execution')).toBe(true)
    expect(unique.has('skills:/availability')).toBe(true)
    expect(unique.has('tools:/edit')).toBe(true)
    expect(unique.has('tools:/execution')).toBe(true)
    expect(unique.has('tools:/invoke')).toBe(true)
    expect(unique.has('tools:/availability')).toBe(true)
    expect(workReturnRoute(origin)).toMatchObject({ destination: 'chat', sessionId: 'conversation-12' })
  })

  it('uses the full-width shared frame for tool execution and keeps controls keyboard reachable', () => {
    const route = createWorkRoute('tools', '["native","inspect"]', undefined, '/invoke')
    const html = renderToStaticMarkup(<WorkRoutes route={route} scope={scope} navigate={() => {}} />)
    expect(html).toContain('data-workspace-mode="full"')
    expect(html).toContain('Run tool')
    expect(html).toContain('<button')
  })

  it('keeps tool provider and name together as the canonical identity', () => {
    const first = workEntry(scope, 'tool', { name: 'inspect', provider: 'native', description: '',
      disabled: false, providerDisabled: false })
    const second = workEntry(scope, 'tool', { name: 'inspect', provider: 'remote', description: '',
      disabled: false, providerDisabled: false })
    expect(first.identity.id).toBe('["native","inspect"]')
    expect(first.identity.id).not.toBe(second.identity.id)
    expect(first.title).toBe('inspect')
  })

  it('reads actual native task and project stores and preserves exact IDs', async () => {
    const client = new WorkClient(scope)
    const tasks = await client.list('task')
    expect(tasks.state).toBe('ready')
    if (!('value' in tasks)) throw new Error('Native tasks were unavailable')
    expect(tasks.value.some(row => row.identity.id === taskId && row.title === 'Source task')).toBe(true)
    const task = await client.detail('task', taskId)
    expect(task).toMatchObject({ state: 'ready', value: { identity: { kind: 'task', id: taskId } } })
    const project = await client.detail('project', projectId)
    expect(project).toMatchObject({ state: 'ready', value: { identity: { kind: 'project', id: projectId } } })
    expect(await client.detail('task', 'unknown-native-task')).toMatchObject({ state: 'unavailable' })
  })

  it('masks a previous record and owner on the first React commit', async () => {
    const webPort = await port()
    const origin = `http://127.0.0.1:${webPort}`
    vite = await createServer({
      configFile: false, root: join(resolve(process.cwd(), '../..'), 'apps/assistant'),
      plugins: [{
        name: 'work-transition',
        resolveId(id) { if (id === '/work-entry.ts') return '\0work-entry' },
        load(id) { if (id === '\0work-entry') return `
import React from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import WorkRoutes, { createWorkRoute } from '/src/features/work/WorkRoutes.web.tsx'
const host = document.getElementById('root')
const root = createRoot(host)
window.route = createWorkRoute
window.commits = []
window.renderWork = (scope, route) => {
  const before = window.commits.length
  flushSync(() => root.render(React.createElement(React.Profiler, {
    id: 'work', onRender: () => window.commits.push({
      heading: host.querySelector('h1')?.textContent ?? '',
      detail: host.querySelector('section')?.textContent ?? '',
      state: host.querySelector('main')?.getAttribute('data-workspace-state') ?? '',
    }),
  }, React.createElement(WorkRoutes, { scope, route, navigate: () => {}, onReturn: () => {} }))))
  return window.commits.slice(before)
}
window.loaded = true
` },
        configureServer(devServer) {
          devServer.middlewares.use('/integration', (_request, response) => {
            response.setHeader('Content-Type', 'text/html; charset=utf-8')
            response.end('<!doctype html><html><body><div id="root"></div><script type="module" src="/work-entry.ts"></script></body></html>')
          })
        },
      }],
      server: { host: '127.0.0.1', port: webPort, strictPort: true, proxy: { '/api': api } },
    })
    await vite.listen()
    const evaluate = await browser(`${origin}/integration`)
    for (let attempt = 0; attempt < 100 && !(await evaluate('window.loaded === true')); attempt++) {
      await new Promise(done => setTimeout(done, 50))
    }
    expect(await evaluate('window.loaded === true')).toBe(true)
    const ownerA = { runtimeOrigin: origin, ownerId: 'owner-a', cacheKey: 'owner-a' }
    const ownerB = { runtimeOrigin: origin, ownerId: 'owner-b', cacheKey: 'owner-b' }
    const taskRoute = createWorkRoute('tasks', taskId)
    const projectRoute = createWorkRoute('projects', projectId)
    await evaluate(`window.renderWork(${JSON.stringify(ownerA)}, ${JSON.stringify(taskRoute)})`)
    for (let attempt = 0; attempt < 100 && !(await evaluate('document.querySelector("section")?.textContent.includes("Source task")')); attempt++) {
      await new Promise(done => setTimeout(done, 50))
    }
    expect(await evaluate('document.querySelector("section")?.textContent.includes("Source task")')).toBe(true)
    const projectCommits = await evaluate(`window.renderWork(${JSON.stringify(ownerA)}, ${JSON.stringify(projectRoute)})`) as
      Array<{ heading: string; detail: string; state: string }>
    expect(projectCommits[0]).toMatchObject({ heading: 'Projects', detail: '', state: 'loading' })
    for (let attempt = 0; attempt < 100 && !(await evaluate('document.querySelector("section")?.textContent.includes("Source project")')); attempt++) {
      await new Promise(done => setTimeout(done, 50))
    }
    expect(await evaluate('document.querySelector("section")?.textContent.includes("Source project")')).toBe(true)
    const ownerCommits = await evaluate(`window.renderWork(${JSON.stringify(ownerB)}, ${JSON.stringify(projectRoute)})`) as
      Array<{ heading: string; detail: string; state: string }>
    expect(ownerCommits[0]).toMatchObject({ heading: 'Projects', detail: '', state: 'loading' })
  }, 30000)

  it('separates empty, stale, denied, unavailable and failed reads', async () => {
    const client = new WorkClient(scope)
    const response = await originalFetch(`${api}/api/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
    expect(response.ok).toBe(true)
    expect((await client.list('task')).state).toBe('empty')
    const exited = new Promise<void>(done => server!.once('exit', () => done()))
    server!.kill('SIGTERM')
    await exited
    expect((await client.list('task')).state).toBe('stale')
    expect((await client.list('project')).state).toBe('failed')
    expect(classifyWorkError(new GatewayError('forbidden', 403))).toBe('denied')
    expect(classifyWorkError(new GatewayError('unavailable', 503))).toBe('unavailable')
  })
})
