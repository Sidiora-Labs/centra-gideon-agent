import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer, type ViteDevServer } from 'vite'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { parseShellRoute, serializeShellRoute } from '../../shared/shell/shellRoutes'
import { browserConversationRoute, browserRoute, isBrowserRoute } from './BrowserRoute.web'

const root = resolve(process.cwd(), '../..')
const children: ChildProcessWithoutNullStreams[] = []
const directories: string[] = []
let vite: ViteDevServer | undefined
let socket: WebSocket | undefined
let api = ''
let origin = ''
let otherToken = ''

async function freePort(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  const address = server.address()
  const result = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(done => server.close(() => done()))
  return result
}

async function browser(address: string): Promise<(expression: string) => Promise<any>> {
  const directory = await mkdtemp(join(tmpdir(), 'gideon-browser-route-'))
  directories.push(directory)
  const port = await freePort()
  const child = spawn(process.env.CHROMIUM_BIN || 'chromium', [
    '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-background-networking',
    '--no-first-run', `--remote-debugging-port=${port}`, `--user-data-dir=${directory}`, 'about:blank',
  ])
  children.push(child)
  let target: { webSocketDebuggerUrl: string } | undefined
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json() as
        Array<{ type: string; webSocketDebuggerUrl: string }>
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
  const pending = new Map<number, { done: (value: any) => void; reject: (error: Error) => void }>()
  socket.addEventListener('message', event => {
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
    socket!.send(JSON.stringify({ id, method, params }))
  })
  await command('Page.navigate', { url: address })
  return async expression => {
    let response: any
    try { response = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }) }
    catch (error) {
      if (error instanceof Error && error.message.includes('Inspected target navigated')) return undefined
      throw error
    }
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text)
    return response.result?.value
  }
}

beforeAll(async () => {
  const port = await freePort()
  origin = `http://127.0.0.1:${port}`
  const server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', [
    join(root, 'apps/assistant/test-support/browser_server.py'), origin,
  ], { env: { ...process.env, PYTHONPATH: join(root, 'runtime') } })
  children.push(server)
  const ready = await new Promise<{ api_port: number; other_token: string }>((done, reject) => {
    let output = ''
    let errors = ''
    const timeout = setTimeout(() => reject(new Error(`Browser server timed out: ${errors}`)), 20000)
    server.stdout.on('data', chunk => {
      output += String(chunk)
      if (output.includes('\n')) {
        clearTimeout(timeout)
        done(JSON.parse(output.split('\n')[0]))
      }
    })
    server.stderr.on('data', chunk => { errors += String(chunk) })
    server.once('exit', code => { clearTimeout(timeout); reject(new Error(`Browser server exited ${code}: ${errors}`)) })
  })
  api = `http://127.0.0.1:${ready.api_port}`
  otherToken = ready.other_token
  vite = await createServer({
    configFile: false, root: join(root, 'apps/assistant'),
    optimizeDeps: { noDiscovery: true, include: ['react', 'react-dom/client', 'react/jsx-dev-runtime', 'react-native-web'] },
    resolve: {
      alias: [
        { find: /^react-native$/, replacement: 'react-native-web' },
        { find: /^react-native-svg$/, replacement: 'react-native-svg/lib/module/ReactNativeSVG.web.js' },
      ],
      extensions: ['.web.mjs', '.mjs', '.web.js', '.js', '.web.tsx', '.tsx', '.web.ts', '.ts', '.json'],
    },
    plugins: [{
      name: 'browser-route-entry',
      resolveId(id) { if (id === '/browser-entry.ts') return '\0browser-entry' },
      load(id) { if (id === '\0browser-entry') return `
import React from 'react'
import { createRoot } from 'react-dom/client'
import BrowserRoute, { browserRoute } from '/src/features/browser/BrowserRoute.web.tsx'
import { BrowserClient } from '/src/features/browser/browserClient.ts'
import { ownerScope, signInOwner } from '/src/shared/auth.web.tsx'
import { ShellThemeProvider } from '/src/shared/shell/shellTheme.web.ts'
const root = createRoot(document.getElementById('root'))
window.signIn = async name => { window.scope = ownerScope(location.origin, await signInOwner(name, 'correct-horse-battery-staple')); return window.scope.ownerId }
window.openBrowser = conversation => root.render(React.createElement(ShellThemeProvider, { initialPreference: 'light' },
  React.createElement(BrowserRoute, { route: browserRoute(conversation), scope: window.scope,
    navigate: route => { window.returnRoute = route } })))
window.client = () => new BrowserClient(window.scope)
window.loaded = true
` },
      configureServer(server) {
        server.middlewares.use('/integration', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><html><body><div id="root"></div><script type="module" src="/browser-entry.ts"></script></body></html>')
        })
      },
    }],
    server: { host: '127.0.0.1', port, strictPort: true, proxy: { '/api': api } },
  })
  await vite.listen()
}, 30000)

afterAll(async () => {
  socket?.close()
  if (vite) await vite.close()
  for (const child of children) child.kill('SIGTERM')
  for (const directory of directories) await rm(directory, { recursive: true, force: true })
})

describe('conversation browser client and route', () => {
  it('keeps the canonical conversation and return context in the shell route', () => {
    const source = { destination: 'chat' as const, sessionId: 'dashboard:collision', selectionId: 'message-3', scrollY: 280 }
    const route = browserRoute('dashboard:collision', source)
    expect(isBrowserRoute(route)).toBe(true)
    expect(parseShellRoute(serializeShellRoute(route))).toEqual(route)
    expect(browserConversationRoute(route)).toMatchObject({ destination: 'chat', sessionId: 'dashboard:collision' })
    expect(isBrowserRoute(browserRoute('channel-thread'))).toBe(true)
  })

  it('uses real authenticated reservations, reconciles a lost response, and isolates owners', async () => {
    const evaluate = await browser(`${origin}/integration`)
    for (let attempt = 0; attempt < 100 && !(await evaluate('window.loaded === true')); attempt++)
      await new Promise(done => setTimeout(done, 50))
    expect(await evaluate('window.signIn("browser-owner")')).toBe('browser-owner')
    await evaluate('window.openBrowser("browser-chat")')
    for (let attempt = 0; attempt < 100 && !(await evaluate('document.querySelector("[aria-label=\\"Browser session\\"]")?.textContent.includes("Ready to connect")')); attempt++)
      await new Promise(done => setTimeout(done, 50))
    expect(await evaluate('document.querySelector("main")?.getAttribute("data-workspace-state")')).toBe('ready')
    const first = await evaluate('(async () => await window.client().open("browser-chat"))()')
    expect(first.state).toBe('ready')
    expect(first.value.status).toBe('reserved')
    expect(await evaluate('(async () => await window.client().open("browser-chat"))()')).toMatchObject({ value: { id: first.value.id } })
    const alias = await evaluate('(async () => await window.client().open("dashboard:browser-chat"))()')
    expect(alias).toMatchObject({ state: 'ready', value: { id: first.value.id, conversationId: 'browser-chat' } })
    expect(browserConversationRoute(browserRoute(alias.value.conversationId)).sessionId).toBe('browser-chat')

    const collisions = await evaluate('(async () => await Promise.all([window.client().open("collision"), window.client().open("dashboard:collision"), window.client().open("channel-thread")]))()')
    expect(collisions.map((row: any) => row.state)).toEqual(['ready', 'ready', 'ready'])
    expect(new Set(collisions.map((row: any) => row.value.id)).size).toBe(3)
    expect(collisions.map((row: any) => row.value.conversationId)).toEqual(['collision', 'dashboard:collision', 'channel-thread'])

    await evaluate('window.openBrowser("collision")')
    for (let attempt = 0; attempt < 100 && !(await evaluate(`document.querySelector('[aria-label="Browser session"]')?.textContent.includes(${JSON.stringify(collisions[0].value.id)})`)); attempt++)
      await new Promise(done => setTimeout(done, 50))
    await evaluate(`(() => {
      window.originalFetch = window.fetch.bind(window)
      window.fetch = async (input, init) => {
        const response = await window.originalFetch(input, init)
        if (String(input) === '/api/browser/sessions/${collisions[0].value.id}/close' && init?.method === 'POST') {
          window.delayedCloseCommitted = true
          return new Promise(resolve => { window.releaseDelayedClose = () => resolve(response) })
        }
        return response
      }
      [...document.querySelectorAll('[aria-label="Browser session"] button')]
        .find(button => button.textContent === 'Close browser').click()
    })()`)
    for (let attempt = 0; attempt < 100 && !(await evaluate('window.delayedCloseCommitted === true')); attempt++)
      await new Promise(done => setTimeout(done, 50))
    expect(await evaluate('window.delayedCloseCommitted === true')).toBe(true)
    await evaluate('window.openBrowser("dashboard:collision")')
    for (let attempt = 0; attempt < 100 && !(await evaluate(`document.querySelector('[aria-label="Browser session"]')?.textContent.includes(${JSON.stringify(collisions[1].value.id)})`)); attempt++)
      await new Promise(done => setTimeout(done, 50))
    await evaluate('window.releaseDelayedClose(); window.fetch = window.originalFetch; true')
    expect(await evaluate(`(() => {
      const section = document.querySelector('[aria-label="Browser session"]')
      return section?.textContent.includes(${JSON.stringify(collisions[1].value.id)}) &&
        [...section.querySelectorAll('button')].find(button => button.textContent === 'Close browser')?.disabled === false
    })()`)).toBe(true)

    const lost = await evaluate(`(async () => {
      const nativeFetch = window.fetch.bind(window)
      let dropped = false
      window.fetch = async (input, init) => {
        const response = await nativeFetch(input, init)
        if (!dropped && String(input) === '/api/browser/sessions' && init?.method === 'POST') {
          dropped = true
          throw new TypeError('Response lost after commit')
        }
        return response
      }
      const result = await window.client().open('lost-reply')
      window.fetch = nativeFetch
      return { result, dropped }
    })()`)
    expect(lost.dropped).toBe(true)
    expect(lost.result).toMatchObject({ state: 'ready', value: { conversationId: 'lost-reply' } })
    expect(lost.result.value.id).not.toBe(first.value.id)
    expect(await evaluate('(async () => await window.client().open("lost-reply"))()'))
      .toMatchObject({ value: { id: lost.result.value.id } })

    const close = await evaluate(`(async () => await window.client().mutate(${JSON.stringify(first.value)}, 'close'))()`)
    expect(close).toMatchObject({ state: 'ready', value: { id: first.value.id, status: 'closed' } })
    const stale = await evaluate(`(async () => await window.client().mutate(${JSON.stringify(first.value)}, 'reopen'))()`)
    expect(stale).toMatchObject({ state: 'stale-version', current: { id: first.value.id, status: 'closed' } })
    const headers = { Cookie: `gideon_token_10021=${otherToken}` }
    const foreign = await fetch(`${api}/api/browser/sessions/${first.value.id}`, { headers })
    const absent = await fetch(`${api}/api/browser/sessions/absent`, { headers })
    expect(foreign.status).toBe(404)
    expect(absent.status).toBe(404)
  }, 45000)
})
