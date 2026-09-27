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
let socket: WebSocket | undefined
afterAll(async () => {
  socket?.close()
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

async function chromium(address: string) {
  const directory = await mkdtemp(join(tmpdir(), 'gideon-activity-detail-'))
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
  socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise<void>((done, reject) => {
    socket!.addEventListener('open', () => done(), { once: true })
    socket!.addEventListener('error', () => reject(new Error('Chromium debugger failed')), { once: true })
  })
  let nextId = 0
  const pending = new Map<number, (value: any) => void>()
  socket.addEventListener('message', event => {
    const response = JSON.parse(String(event.data)) as { id?: number; result?: any }
    if (response.id) { pending.get(response.id)?.(response); pending.delete(response.id) }
  })
  const command = (method: string, params: Record<string, unknown> = {}) => new Promise<any>(done => {
    const id = ++nextId; pending.set(id, done); socket!.send(JSON.stringify({ id, method, params }))
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

async function until(evaluate: (expression: string) => Promise<any>, expression: string): Promise<void> {
  for (let attempt = 0; attempt < 200; attempt++) {
    if (await evaluate(expression)) return
    await new Promise(done => setTimeout(done, 50))
  }
  throw new Error(`Activity detail condition did not appear: ${expression}`)
}

describe('Gideon Activity detail routes', () => {
  it('opens a native card, serializes return state, and resolves a fresh detail after direct route load', async () => {
    const webPort = await port()
    const origin = `http://127.0.0.1:${webPort}`
    const viteCache = await mkdtemp(join(tmpdir(), 'gideon-activity-vite-'))
    directories.push(viteCache)
    const child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3',
      [join(root, 'apps/assistant/test-support/activity_server.py'), origin],
      { env: { ...process.env, PYTHONPATH: join(root, 'runtime') } })
    children.push(child)
    const line = await new Promise<string>((done, reject) => {
      let output = ''; let errors = ''
      const timeout = setTimeout(() => reject(new Error(`Activity API timed out: ${errors}`)), 15000)
      child.stdout.on('data', chunk => { output += String(chunk); if (output.includes('\n')) { clearTimeout(timeout); done(output.split('\n')[0]) } })
      child.stderr.on('data', chunk => { errors += String(chunk) })
      child.once('exit', code => { clearTimeout(timeout); reject(new Error(`Activity API exited ${code}: ${errors}`)) })
    })
    const api = JSON.parse(line) as { api_port: number; control_port: number }
    vite = await createServer({
      configFile: false, root: join(root, 'apps/assistant'),
      cacheDir: viteCache,
      resolve: { alias: { 'react-native': 'react-native-web' } },
      optimizeDeps: { include: ['react', 'react-dom', 'react-dom/client', 'react-native-web'] },
      plugins: [{
        name: 'activity-detail-route-integration',
        resolveId(id) { if (id === '/activity-detail-entry.tsx') return '\0activity-detail-entry' },
        load(id) { if (id === '\0activity-detail-entry') return `
import React from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import ActivityScreen from '/src/features/activity/ActivityScreen.tsx'
import ActivityDetail from '/src/features/activity/ActivityDetail.web.tsx'
import { activityModuleDefinitions } from '/src/features/activity/moduleDefinitions.web.ts'
import { signInOwner, ownerScope } from '/src/shared/auth.web.tsx'
import { createShellRoute, serializeShellRoute, parseShellRoute } from '/src/shared/shell/shellRoutes.ts'
import { ShellThemeProvider } from '/src/shared/shell/shellTheme.web.ts'
const nativeFetch = window.fetch.bind(window)
window.notificationReads = 0
window.fetch = (input, init) => {
  const url = new URL(typeof input === 'string' ? input : input.url, location.href)
  if (url.pathname === '/api/notifications') window.notificationReads++
  return nativeFetch(input, init)
}
const root = createRoot(document.getElementById('root'))
window.activityModuleDefinitions = activityModuleDefinitions
window.beginJourney = async () => {
  const owner = await signInOwner('owner-a', 'correct-horse-battery-staple')
  const scope = ownerScope(location.origin, owner)
  const route = createShellRoute('activity', { sessionId: 'origin-chat', placement: { id: 'activity', query: { source: 'notification' } } })
  window.scope = scope; window.route = route
window.navigateShell = async next => {
    const url = serializeShellRoute(next)
    const parsed = parseShellRoute(url, location.origin)
    if (parsed.kind !== 'route') throw new Error('Serialized detail route did not parse')
    history.pushState(null, '', url)
    window.route = parsed
    const activeScope = window.currentScope || scope
    const module = activityModuleDefinitions.find(item => item.matches(parsed))
    window.resolved = module ? await module.resolve(activeScope, parsed) : 'unavailable'
    if (parsed.destination === 'activity' && parsed.view === 'list') {
      flushSync(() => root.render(React.createElement(ShellThemeProvider, null,
        React.createElement(ActivityScreen, { route: parsed, scope: activeScope,
          navigate: window.navigateShell, onReturn: () => {} }))))
    } else {
      flushSync(() => root.render(React.createElement(ShellThemeProvider, null,
        React.createElement(ActivityDetail, { route: parsed, scope: activeScope,
          navigate: window.navigateShell, onReturn: () => {} }))))
    }
  }
  window.currentScope = scope
  window.openNativeDetail = window.navigateShell
  window.mountNotificationOwnerA = () => {
    const detailRoute = createShellRoute('activity', { view: 'detail',
      record: { kind: 'notification', id: 'notification-1' }, returnTo: { destination: 'activity' } })
    window.route = detailRoute
    flushSync(() => root.render(React.createElement(ShellThemeProvider, null,
      React.createElement(ActivityDetail, { route: detailRoute, scope,
        navigate: window.navigateShell, onReturn: () => {} }))))
  }
  window.switchOwner = async () => {
    window.currentScope = ownerScope(location.origin, { user: 'owner-b' })
    flushSync(() => root.render(React.createElement(ShellThemeProvider, null,
      React.createElement(ActivityDetail, { route: window.route, scope: window.currentScope,
        navigate: window.navigateShell, onReturn: () => {} }))))
  }
  flushSync(() => root.render(React.createElement(ShellThemeProvider, null,
    React.createElement(ActivityScreen, { route, scope, navigate: window.navigateShell, onReturn: () => {} }))))
}
window.loaded = true
` },
        configureServer(server) { server.middlewares.use('/integration', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><html><head><style>html,body,#root{margin:0;width:100%;min-height:1800px;display:flex}</style></head><body><div id="root"></div><script type="module" src="/activity-detail-entry.tsx"></script></body></html>')
        }) },
      }],
      server: { host: '127.0.0.1', port: webPort, strictPort: true,
        proxy: { '/api': `http://127.0.0.1:${api.api_port}` } },
    })
    await vite.listen()
    const { evaluate } = await chromium(`${origin}/integration`)
    await until(evaluate, 'window.loaded === true')
    await fetch(`http://127.0.0.1:${api.control_port}/trigger-pages`, { method: 'POST' })
    await evaluate('window.beginJourney()')
    await until(evaluate, `!!document.querySelector('[data-source="notification"] [data-open-activity-id="notification-1"]')`)
    await evaluate('window.scrollTo(0, 240)')
    await evaluate(`document.querySelector('[data-source="notification"] [data-open-activity-id="notification-1"]').click()`)
    await until(evaluate, `document.querySelector('[data-activity-detail="notification"]')?.getAttribute('data-read-state') === 'ready'`)
    expect(await evaluate(`window.resolved`)).toBe('available')
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('Receipt available')`)).toBe(true)
    expect(await evaluate(`window.route.record.id`)).toBe('notification-1')
    expect(await evaluate(`window.route.returnTo.sessionId`)).toBe('origin-chat')
    expect(await evaluate(`window.route.returnTo.placement.query.source`)).toBe('notification')
    expect(await evaluate(`window.route.returnTo.placement.query.selected`)).toBe('notification-1')
    expect(await evaluate(`window.route.returnTo.placement.query.scroll`)).toMatch(/^\d+$/)
    expect(await evaluate(`window.activityModuleDefinitions[1].matches(window.route)`)).toBe(true)

    await evaluate(`document.querySelector('[aria-label="Back"]')?.click()`)
    await until(evaluate, `window.route.view === 'list' && !!document.querySelector('[data-activity-id="notification-1"]')`)
    expect(await evaluate(`window.resolved`)).toBe('available')
    expect(await evaluate(`document.querySelector('[data-activity-id="notification-1"]')?.getAttribute('data-selected')`)).toBe('true')
    expect(await evaluate(`Math.abs(window.scrollY - 240) < 2`)).toBe(true)

    await evaluate(`window.openNativeDetail({ destination: 'activity', view: 'detail',
      record: { kind: 'trigger_run', id: 'trigger-page-119' }, returnTo: { destination: 'activity',
        sessionId: 'origin-chat', placement: { id: 'activity' } } })`)
    await until(evaluate, `document.querySelector('[data-activity-detail="trigger_run"]')?.getAttribute('data-read-state') === 'ready'`)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('trigger-page-119')`)).toBe(true)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('Native trigger run')`)).toBe(true)

    await evaluate(`window.openNativeDetail({ destination: 'activity', view: 'detail',
      record: { kind: 'trigger_run', id: 'event:event-1:summary' }, returnTo: { destination: 'activity' } })`)
    await until(evaluate, `document.querySelector('[data-activity-detail="trigger_run"]')?.getAttribute('data-read-state') === 'ready'`)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('Event trigger summary')`)).toBe(true)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('event-1')`)).toBe(true)

    await evaluate(`window.openNativeDetail({ destination: 'activity', view: 'detail',
      record: { kind: 'artifact', id: 'native-result' }, returnTo: { destination: 'activity' } })`)
    await until(evaluate, `document.querySelector('[data-activity-detail="artifact"]')?.getAttribute('data-read-state') === 'ready'`)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('Native result')`)).toBe(true)
    await evaluate(`window.openNativeDetail({ destination: 'activity', view: 'detail',
      record: { kind: 'artifact', id: 'different-native-id' }, returnTo: { destination: 'activity' } })`)
    await until(evaluate, `document.querySelector('[data-activity-detail="artifact"]')?.getAttribute('data-read-state') === 'missing'`)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('Record unavailable')`)).toBe(true)

    await evaluate('window.mountNotificationOwnerA()')
    await until(evaluate, `document.querySelector('[data-activity-detail="notification"]')?.getAttribute('data-read-state') === 'ready'`)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('Receipt available')`)).toBe(true)
    await fetch(`http://127.0.0.1:${api.control_port}/delay`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seconds: 30, requests: 1, hold: true, path: '/api/notifications' }) })
    await evaluate('window.notificationReads = 0')
    await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent?.includes('Reload record'))?.click()`)
    const delayStateUrl = `http://127.0.0.1:${api.control_port}/delay-state`
    let delayState: { entered: number; completed: number } = { entered: 0, completed: 0 }
    for (let attempt = 0; attempt < 200 && delayState.entered !== 1; attempt++) {
      delayState = await (await fetch(delayStateUrl)).json() as typeof delayState
      if (delayState.entered !== 1) await new Promise(done => setTimeout(done, 25))
    }
    expect(delayState.entered).toBe(1)
    expect(delayState.completed).toBe(0)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('Receipt available')`)).toBe(false)
    await evaluate('window.switchOwner()')
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('Receipt available')`)).toBe(false)
    await until(evaluate, `document.querySelector('[data-activity-detail="notification"]')?.getAttribute('data-read-state') === 'denied'`)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('Receipt available')`)).toBe(false)
    delayState = await (await fetch(delayStateUrl)).json() as typeof delayState
    expect(delayState.completed).toBe(0)
    await fetch(`http://127.0.0.1:${api.control_port}/delay-release`, { method: 'POST' })
    for (let attempt = 0; attempt < 200; attempt++) {
      delayState = await (await fetch(delayStateUrl)).json() as typeof delayState
      if (delayState.completed === 1) break
      await new Promise(done => setTimeout(done, 25))
    }
    expect(delayState.completed).toBe(1)
    await new Promise(done => setTimeout(done, 100))
    expect(await evaluate(`document.querySelector('[data-activity-detail="notification"]')?.getAttribute('data-read-state')`)).toBe('denied')
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('Receipt available')`)).toBe(false)
  }, 30_000)
})
