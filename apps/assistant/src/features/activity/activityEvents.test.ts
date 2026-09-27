import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer, type ViteDevServer } from 'vite'
import { afterAll, describe, expect, it } from 'vitest'
import type { WsMessage } from '../../../../console/src/shared/data/socketTransport'
import { ACTIVITY_EVENT_RETENTION, ActivityEventWindow } from './activityEvents'

const root = resolve(process.cwd(), '../..')
const children: ChildProcessWithoutNullStreams[] = []
const directories: string[] = []
let vite: ViteDevServer | undefined
let browserSocket: WebSocket | undefined

afterAll(async () => {
  browserSocket?.close()
  if (vite) await vite.close()
  for (const child of children) {
    if (child.exitCode !== null || child.signalCode !== null) continue
    const closed = new Promise<void>(done => child.once('close', () => done()))
    child.kill('SIGTERM')
    await Promise.race([closed, new Promise<void>(done => setTimeout(done, 3_000))])
  }
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
    { env: { ...process.env, PYTHONPATH: join(root, 'runtime') } })
  children.push(child)
  const line = await new Promise<string>((done, reject) => {
    let output = ''; let errors = ''
    const timeout = setTimeout(() => reject(new Error(`Activity server timed out: ${errors}`)), 15_000)
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

async function browser(address: string) {
  const directory = await mkdtemp(join(tmpdir(), 'gideon-activity-events-'))
  directories.push(directory)
  const debugPort = await port()
  const child = spawn(process.env.CHROMIUM_BIN || 'chromium', ['--headless', '--no-sandbox', '--disable-gpu',
    '--disable-background-networking', `--remote-debugging-port=${debugPort}`, `--user-data-dir=${directory}`, 'about:blank'])
  children.push(child)
  let target: { webSocketDebuggerUrl: string } | undefined
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json() as Array<{ type: string; webSocketDebuggerUrl: string }>
      target = targets.find(item => item.type === 'page')
      if (target) break
    } catch { await new Promise(done => setTimeout(done, 100)) }
  }
  if (!target) throw new Error('Chromium did not start')
  browserSocket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise<void>((done, reject) => {
    browserSocket!.addEventListener('open', () => done(), { once: true })
    browserSocket!.addEventListener('error', () => reject(new Error('Chromium debugger failed')), { once: true })
  })
  let nextId = 0
  const pending = new Map<number, (value: any) => void>()
  browserSocket.addEventListener('message', event => {
    const response = JSON.parse(String(event.data)) as { id?: number; result?: any }
    if (response.id) { pending.get(response.id)?.(response); pending.delete(response.id) }
  })
  const command = (method: string, params: Record<string, unknown> = {}) => new Promise<any>(done => {
    const id = ++nextId; pending.set(id, done); browserSocket!.send(JSON.stringify({ id, method, params }))
  })
  await command('Page.navigate', { url: address })
  const evaluate = async (expression: string) => {
    const response = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (response?.error) throw new Error(response.error.message)
    if (response?.result?.exceptionDetails) throw new Error(response.result.exceptionDetails.exception?.description || response.result.exceptionDetails.text)
    return response?.result?.result?.value
  }
  return { evaluate }
}

async function until(evaluate: (expression: string) => Promise<any>, expression: string, timeoutMs = 20_000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return
    await new Promise(done => setTimeout(done, 50))
  }
  throw new Error(`Activity event condition did not appear: ${expression}`)
}

async function control(address: string, route: string, body: object): Promise<any> {
  const response = await fetch(`${address}${route}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) })
  if (!response.ok) throw new Error(`Native fixture control ${route} returned ${response.status}`)
  return response.json()
}

async function controlState(address: string, route: string): Promise<any> {
  const response = await fetch(`${address}${route}`)
  if (!response.ok) throw new Error(`Native fixture control ${route} returned ${response.status}`)
  return response.json()
}

describe('Activity event invalidation and snapshot recovery', () => {
  it('merges real native hints through canonical reads and bounds event state', async () => {
    const dedupe = new ActivityEventWindow()
    const older: WsMessage = { type: 'inbox_new_item', data: { id: 'native-old', created_at: 100 } }
    const newer: WsMessage = { type: 'inbox_new_item', data: { id: 'native-new', created_at: 200 } }
    expect(dedupe.accept(older, 1000)).toBe(true)
    expect(dedupe.accept(newer, 1001)).toBe(true)
    expect(dedupe.accept(newer, 1002)).toBe(false)
    expect(dedupe.accept({ type: 'chat_segment', data: { session: 'chat-1' } }, 1003)).toBe(false)
    for (let index = 0; index < ACTIVITY_EVENT_RETENTION + 20; index++) {
      expect(dedupe.accept({ type: 'approval', data: { id: `approval-${index}`, revision: String(index) } }, 3000 + index)).toBe(true)
    }
    expect(dedupe.size).toBe(ACTIVITY_EVENT_RETENTION)

    const webPort = await port()
    const origin = `http://127.0.0.1:${webPort}`
    const server = await nativeServer(origin)
    const viteCache = await mkdtemp(join(tmpdir(), 'gideon-activity-events-vite-'))
    directories.push(viteCache)
    vite = await createServer({
      configFile: false, root: join(root, 'apps/assistant'), cacheDir: viteCache,
      resolve: { alias: { 'react-native': 'react-native-web' } },
      optimizeDeps: { include: ['react', 'react-dom', 'react-dom/client', 'react-native-web'] },
      plugins: [{
        name: 'activity-events-integration',
        resolveId(id) { if (id === '/activity-events-entry.tsx') return '\0activity-events-entry' },
        load(id) { if (id === '\0activity-events-entry') return `
import React from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import ActivityScreen from '/src/features/activity/ActivityScreen.tsx'
import { signInOwner, ownerScope } from '/src/shared/auth.web.tsx'
import { createShellRoute } from '/src/shared/shell/shellRoutes.ts'
import { ShellThemeProvider } from '/src/shared/shell/shellTheme.web.ts'
const root = createRoot(document.getElementById('root'))
window.mountActivity = scope => {
  window.scope = scope
  window.route = createShellRoute('activity')
  flushSync(() => root.render(React.createElement(ShellThemeProvider, null,
    React.createElement(ActivityScreen, { route: window.route, scope, navigate: () => {}, onReturn: () => {} }))))
}
window.beginActivity = async username => {
  const owner = await signInOwner(username, 'correct-horse-battery-staple')
  window.mountActivity(ownerScope(location.origin, owner))
}
window.returnToList = () => {
  flushSync(() => root.render(null))
  flushSync(() => root.render(React.createElement(ShellThemeProvider, null,
    React.createElement(ActivityScreen, { route: window.route, scope: window.scope, navigate: () => {}, onReturn: () => {} }))))
}
window.unmountActivity = () => flushSync(() => root.render(null))
window.loaded = true
` },
        configureServer(devServer) { devServer.middlewares.use('/integration', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><html><head><style>html,body,#root{margin:0;width:100%;height:100%;display:flex;overflow:hidden}</style></head><body><div id="root"></div><script type="module" src="/activity-events-entry.tsx"></script></body></html>')
        }) },
      }],
      server: { host: '127.0.0.1', port: webPort, strictPort: true, proxy: { '/api': { target: server.api, ws: true } } },
    })
    await vite.listen()
    const { evaluate } = await browser(`${origin}/integration`)
    await until(evaluate, 'window.loaded === true')
    await evaluate(`window.beginActivity('owner-a')`)
    await until(evaluate, `document.querySelector('[data-source-health="task"]')?.getAttribute('data-phase') === 'ready'`)
    await until(evaluate, `document.querySelector('[data-activity-id="notification-1"]') !== null`)

    const notes = await control(server.control, '/out-of-order-notes', {}) as { ids: string[] }
    expect(notes.ids).toHaveLength(2)
    await until(evaluate, `document.querySelector('[data-activity-id="${notes.ids[0]}"]') !== null`)
    await until(evaluate, `document.querySelector('[data-activity-id="${notes.ids[1]}"]') !== null`)
    const stableKeys = await evaluate(`(${JSON.stringify(notes.ids)}).map(id => document.querySelector('[data-activity-id="' + id + '"]').getAttribute('data-activity-key'))`)
    expect(new Set(stableKeys).size).toBe(2)
    expect(await evaluate(`(${JSON.stringify(notes.ids)}).map(id => document.querySelectorAll('[data-activity-id="' + id + '"]').length)`)).toEqual([1, 1])

    await control(server.control, '/delay', { seconds: 0.01, path: '/api/tasks', requests: 1, hold: true })
    const burstNotes = await control(server.control, '/out-of-order-notes', {}) as { ids: string[] }
    const burstDeadline = Date.now() + 5_000
    let delayed = await controlState(server.control, '/delay-state') as { entered: number }
    while (delayed.entered < 1 && Date.now() < burstDeadline) {
      await new Promise(done => setTimeout(done, 50))
      delayed = await controlState(server.control, '/delay-state')
    }
    expect(delayed.entered).toBe(1)
    await control(server.control, '/delay-release', {})
    await until(evaluate, `Array.from(document.querySelectorAll('[data-source="inbox_item"]')).some(card => card.textContent.includes('Older live note'))`)
    await until(evaluate, `Array.from(document.querySelectorAll('[data-source="inbox_item"]')).some(card => card.textContent.includes('Newer live note'))`)
    expect(await evaluate(`(${JSON.stringify(burstNotes.ids)}).map(id => document.querySelectorAll('[data-activity-id="' + id + '"]').length)`)).toEqual([1, 1])

    await control(server.control, '/websocket-offline', { offline: true })
    await until(evaluate, `document.querySelector('[data-source-health="task"]')?.getAttribute('data-freshness') === 'stale'`)
    await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent.trim() === 'Refresh').click()`)
    await until(evaluate, `document.querySelector('[data-source-health="task"]')?.getAttribute('data-phase') === 'ready'`)
    expect(await evaluate(`document.querySelector('[data-source-health="task"]')?.getAttribute('data-freshness')`)).toBe('stale')
    expect(await evaluate(`document.querySelector('[data-source-health="task"]')?.textContent.includes('Live Activity updates are disconnected')`)).toBe(true)
    await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent.trim() === 'Load more Tasks').click()`)
    await until(evaluate, `document.querySelector('[data-source-health="task"]')?.getAttribute('data-phase') === 'ready'
      && document.querySelector('[data-source-health="task"]')?.getAttribute('data-freshness') === 'stale'`)
    expect(await evaluate(`document.querySelector('[data-source-health="task"]')?.textContent.includes('Live Activity updates are disconnected')`)).toBe(true)
    await control(server.control, '/task', { title: 'Recovered after reconnect' })
    await control(server.control, '/websocket-offline', { offline: false })
    await until(evaluate, `Array.from(document.querySelectorAll('[data-source="task"]')).some(card => card.textContent.includes('Recovered after reconnect'))`)
    expect(await evaluate(`document.querySelector('[data-source-health="task"]')?.getAttribute('data-freshness')`)).toBe('current')

    const createdAt = Date.now()
    await control(server.control, '/task', { title: 'Recovered by periodic snapshot' })
    await until(evaluate, `Array.from(document.querySelectorAll('[data-source="task"]')).some(card => card.textContent.includes('Recovered by periodic snapshot'))`, 20_000)
    expect(Date.now() - createdAt).toBeLessThan(20_000)

    await control(server.control, '/task', { title: 'Recovered on list return' })
    await evaluate('window.returnToList()')
    await until(evaluate, `Array.from(document.querySelectorAll('[data-source="task"]')).some(card => card.textContent.includes('Recovered on list return'))`)

    await evaluate(`window.mountActivity({ runtimeOrigin: location.origin, ownerId: 'owner-b',
      cacheKey: JSON.stringify([location.origin, 'owner-b']) })`)
    expect(await evaluate(`Array.from(document.querySelectorAll('[data-source="task"]')).some(card => card.textContent.includes('Native task 0'))`)).toBe(false)
    await evaluate('window.unmountActivity()')
  }, 60_000)
})
