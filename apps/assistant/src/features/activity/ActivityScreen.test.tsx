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
    { env: { ...process.env, PYTHONPATH: join(root, 'runtime') } })
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

async function browser(address: string): Promise<{
  evaluate: (expression: string) => Promise<any>
  command: (method: string, params?: Record<string, unknown>) => Promise<any>
}> {
  const directory = await mkdtemp(join(tmpdir(), 'gideon-activity-screen-'))
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
  return { command, evaluate: async expression => {
    const response = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text)
    return response.result?.value
  } }
}

async function until(evaluate: (expression: string) => Promise<any>, expression: string): Promise<void> {
  for (let attempt = 0; attempt < 200; attempt++) {
    if (await evaluate(expression)) return
    await new Promise(done => setTimeout(done, 50))
  }
  const page = await evaluate('({ loaded: window.loaded, text: document.body.textContent?.slice(0, 500) })')
  throw new Error(`Activity condition did not appear: ${expression}; page=${JSON.stringify(page)}`)
}

async function control(address: string, route: string, body: object): Promise<void> {
  const response = await fetch(`${address}${route}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) })
  expect(response.status).toBe(200)
}

describe('Gideon Activity overview', () => {
  it('renders native attention lanes, mirror collapse, filters, keyboard summaries and source health', async () => {
    const webPort = await port()
    const origin = `http://127.0.0.1:${webPort}`
    const server = await nativeServer(origin)
    vite = await createServer({
      configFile: false, root: join(root, 'apps/assistant'),
      resolve: { alias: { 'react-native': 'react-native-web' } },
      optimizeDeps: { include: ['react', 'react-dom', 'react-dom/client', 'react-native-web'] },
      plugins: [{
        name: 'activity-screen-integration',
        resolveId(id) { if (id === '/activity-screen-entry.tsx') return '\0activity-screen-entry' },
        load(id) { if (id === '\0activity-screen-entry') return `
import React from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import ActivityScreen from '/src/features/activity/ActivityScreen.tsx'
import { activityModuleDefinitions } from '/src/features/activity/moduleDefinitions.web.ts'
import { signInOwner, ownerScope } from '/src/shared/auth.web.tsx'
import { createShellRoute } from '/src/shared/shell/shellRoutes.ts'
import { ShellThemeProvider } from '/src/shared/shell/shellTheme.web.ts'
const root = createRoot(document.getElementById('root'))
window.activityModule = activityModuleDefinitions[0]
window.beginActivity = async () => {
  const owner = await signInOwner('owner-a', 'correct-horse-battery-staple')
  const scope = ownerScope(location.origin, owner)
  const route = createShellRoute('activity')
  window.scope = scope
  window.route = route
  flushSync(() => root.render(React.createElement(ShellThemeProvider, null,
    React.createElement(ActivityScreen, { route, scope, navigate: () => {}, onReturn: () => {} }))))
}
window.loaded = true
` },
        configureServer(devServer) {
          devServer.middlewares.use('/integration', (_request, response) => {
            response.setHeader('Content-Type', 'text/html; charset=utf-8')
            response.end('<!doctype html><html><head><style>html,body,#root{margin:0;width:100%;height:100%;display:flex;overflow:hidden}</style></head><body><div id="root"></div><script type="module" src="/activity-screen-entry.tsx"></script></body></html>')
          })
        },
      }],
      server: { host: '127.0.0.1', port: webPort, strictPort: true, proxy: { '/api': server.api } },
    })
    await vite.listen()
    const { evaluate, command } = await browser(`${origin}/integration`)
    await until(evaluate, 'window.loaded === true')
    await evaluate('window.beginActivity()')
    await until(evaluate, `document.querySelector('[data-source-health="task"]')?.getAttribute('data-phase') === 'ready'`)
    expect(await evaluate(`document.querySelectorAll('[data-source="approval"]').length`)).toBe(1)
    expect(await evaluate(`document.querySelectorAll('[data-source="inbox_item"]').length`)).toBe(0)
    expect(await evaluate(`document.querySelectorAll('[data-source="notification"]').length`)).toBe(1)
    expect(await evaluate(`document.querySelector('[data-source="notification"] button')?.textContent`)).toBe('Show receipt')
    expect(await evaluate(`document.querySelector('[data-source="notification"]')?.textContent.includes('Native status: notice')`)).toBe(true)
    expect(await evaluate(`document.querySelector('[data-activity-lane="attention"]') !== null`)).toBe(true)
    expect(await evaluate(`document.querySelector('[data-activity-lane="working"]') !== null`)).toBe(true)
    expect(await evaluate(`document.querySelector('[data-activity-lane="finished"]') !== null`)).toBe(true)
    expect(await evaluate(`document.querySelector('[data-source-health="chat_session"]')?.textContent.includes('No read route')`)).toBe(true)
    expect(await evaluate(`window.activityModule.matches(window.route)`)).toBe(true)
    expect(await evaluate(`window.activityModule.matches({ ...window.route, view: 'detail', record: { kind: 'task', id: 'task-1' } })`)).toBe(false)

    await until(evaluate, `Array.from(document.querySelectorAll('[data-source-health]')).every(source =>
      !['idle', 'loading'].includes(source.getAttribute('data-phase')))`)
    await evaluate(`(() => {
      window.activityKeyboardEvents = []
      for (const type of ['keydown', 'click']) document.addEventListener(type, event => {
        window.activityKeyboardEvents.push({ type, key: event.key,
          approval: !!event.target.closest?.('[data-source="approval"] button'), trusted: event.isTrusted })
      }, { capture: true })
      document.querySelector('[data-source="approval"] button').focus()
    })()`)
    expect(await evaluate(`document.activeElement === document.querySelector('[data-source="approval"] button')`)).toBe(true)
    await command('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter',
      text: '\r', unmodifiedText: '\r', windowsVirtualKeyCode: 13 })
    await command('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 })
    expect(await evaluate(`window.activityKeyboardEvents.some(event => event.type === 'keydown'
      && event.key === 'Enter' && event.approval && event.trusted)`)).toBe(true)
    expect(await evaluate(`window.activityKeyboardEvents`)).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'click', approval: true, trusted: true }),
    ]))
    await until(evaluate, `document.querySelector('[data-source="approval"] button')?.getAttribute('aria-expanded') === 'true'`)
    expect(await evaluate(`document.querySelector('[data-source="approval"] button').getAttribute('aria-expanded')`)).toBe('true')
    expect(await evaluate(`document.querySelector('[data-source="approval"] [role="region"]').textContent.includes('inbox-1')`)).toBe(true)

    await evaluate(`(() => { const select = document.querySelector('select'); select.value = 'inbox_item';
      select.dispatchEvent(new Event('change', { bubbles: true })); return true })()`)
    await until(evaluate, `document.querySelector('select')?.value === 'inbox_item'`)
    expect(await evaluate(`document.querySelectorAll('[data-source="approval"]').length`)).toBe(1)
    await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Finished').click()`)
    await until(evaluate, `document.querySelector('[data-activity-empty="filtered"]') !== null`)
    expect(await evaluate(`document.querySelector('[data-activity-empty="filtered"]') !== null`)).toBe(true)

    await command('Emulation.setDeviceMetricsOverride', { width: 390, height: 800, deviceScaleFactor: 1, mobile: true })
    expect(await evaluate(`(() => { const body = document.querySelector('[data-workspace-scroll]');
      return body.scrollWidth <= body.clientWidth + 1 })()`)).toBe(true)
    await command('Emulation.setDeviceMetricsOverride', { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false })
    expect(await evaluate(`(() => { const body = document.querySelector('[data-workspace-scroll]');
      return body.scrollWidth <= body.clientWidth + 1 })()`)).toBe(true)

    await control(server.control, '/empty-inbox', {})
    await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Refresh').click()`)
    await until(evaluate, `document.querySelector('[data-source-health="inbox_item"]')?.getAttribute('data-phase') === 'empty'`)
    expect(await evaluate(`document.querySelector('[data-source-health="inbox_item"]')?.textContent.includes('Empty after a successful read')`)).toBe(true)
    await control(server.control, '/workflow-store', { unavailable: true })
    await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Refresh').click()`)
    await until(evaluate, `document.querySelector('[data-source-health="workflow_run"]')?.getAttribute('data-phase') === 'failed'`)
    expect(await evaluate(`document.querySelector('[data-source-health="workflow_run"]')?.getAttribute('data-freshness')`)).toBe('stale')
    expect(await evaluate(`document.querySelector('[data-source-health="workflow_run"]')?.textContent.includes('Read failed')`)).toBe(true)
    expect(await evaluate(`document.querySelector('[data-source-health="task"]')?.getAttribute('data-phase')`)).toBe('ready')
  }, 30000)
})
