import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer, type ViteDevServer } from 'vite'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

const root = resolve(process.cwd(), '../..')
const children: ChildProcessWithoutNullStreams[] = []
const directories: string[] = []
let vite: ViteDevServer | undefined
let socket: WebSocket | undefined
let origin = ''
let fixture: { api_port: number; owned_url: string; blocked_url: string };
let rotateOwner: (username: string) => Promise<void> = async () => { throw new Error('Browser test server is not ready') };

async function freePort(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(done => server.close(() => done()))
  return port
}

async function evaluator(address: string): Promise<(expression: string) => Promise<any>> {
  const directory = await mkdtemp(join(tmpdir(), 'gideon-browser-controls-'))
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
    const response = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
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
  fixture = await new Promise<typeof fixture>((done, reject) => {
    let output = ''
    let errors = ''
    let pendingRotation: { username: string; resolve: () => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> } | undefined
    const timeout = setTimeout(() => reject(new Error(`Browser server timed out: ${errors}`)), 20000)
    server.stdout.on('data', chunk => {
      output += String(chunk)
      const lines = output.split('\n')
      output = lines.pop() || ''
      for (const line of lines) {
        if (!line) continue
        try {
          const message = JSON.parse(line) as typeof fixture & { owner_rotated?: string; control_error?: string }
          if (message.api_port) { clearTimeout(timeout); done(message) }
          else if (pendingRotation && message.owner_rotated === pendingRotation.username) {
            clearTimeout(pendingRotation.timer)
            pendingRotation.resolve()
            pendingRotation = undefined
          } else if (pendingRotation && message.control_error) {
            clearTimeout(pendingRotation.timer)
            pendingRotation.reject(new Error(message.control_error))
            pendingRotation = undefined
          }
        } catch { /* Ignore non-JSON server diagnostics. */ }
      }
    })
    server.stderr.on('data', chunk => { errors += String(chunk) })
    server.once('exit', code => {
      clearTimeout(timeout)
      reject(new Error(`Browser server exited ${code}: ${errors}`))
      if (pendingRotation) {
        clearTimeout(pendingRotation.timer)
        pendingRotation.reject(new Error(`Browser server exited ${code}: ${errors}`))
        pendingRotation = undefined
      }
    })
    rotateOwner = username => {
      if (pendingRotation) return Promise.reject(new Error('An owner rotation is already pending'))
      return new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => {
          pendingRotation = undefined
          reject(new Error(`Credential rotation timed out for ${username}`))
        }, 15000)
        pendingRotation = { username, resolve, reject, timer }
        server.stdin.write(`${JSON.stringify({ rotate_owner: username })}\n`)
      })
    }
  })
  const cacheDir = await mkdtemp(join(tmpdir(), 'gideon-browser-controls-vite-'))
  directories.push(cacheDir)
  vite = await createServer({
    configFile: false, root: join(root, 'apps/assistant'), cacheDir,
    optimizeDeps: { noDiscovery: true, include: ['react', 'react-dom/client', 'react/jsx-dev-runtime', 'react-native-web'] },
    resolve: {
      alias: [
        { find: /^react-native$/, replacement: 'react-native-web' },
        { find: /^react-native-svg$/, replacement: 'react-native-svg/lib/module/ReactNativeSVG.web.js' },
      ],
      extensions: ['.web.mjs', '.mjs', '.web.js', '.js', '.web.tsx', '.tsx', '.web.ts', '.ts', '.json'],
    },
    plugins: [{
      name: 'browser-controls-entry',
      resolveId(id) { if (id === '/browser-controls-entry.ts') return '\0browser-controls-entry' },
      load(id) { if (id === '\0browser-controls-entry') return `
import React from 'react'
import { createRoot } from 'react-dom/client'
import BrowserControls from '/src/features/browser/BrowserControls.web.tsx'
import { BrowserClient } from '/src/features/browser/browserClient.ts'
import { ownerScope, readOwnerSession, signInOwner } from '/src/shared/auth.web.tsx'
import { ShellThemeProvider } from '/src/shared/shell/shellTheme.web.ts'
const root = createRoot(document.getElementById('root'))
window.sessionWaiters = []
window.sessionChanges = 0
window.nextSession = (status, afterVersion) => new Promise((resolve, reject) => {
  const waiter = value => {
    if (value.status !== status || value.version <= afterVersion) return
    clearTimeout(timeout)
    window.sessionWaiters = window.sessionWaiters.filter(item => item !== waiter)
    resolve(value)
  }
  const timeout = setTimeout(() => {
    window.sessionWaiters = window.sessionWaiters.filter(item => item !== waiter)
    reject(new Error('Session update did not arrive: ' + status + ' after version ' + afterVersion))
  }, 30000)
  window.sessionWaiters.push(waiter)
})
window.signIn = async () => {
  const login = await signInOwner('browser-owner', 'correct-horse-battery-staple')
  const owner = await readOwnerSession()
  if (login.user !== 'browser-owner' || owner.user !== 'browser-owner') throw new Error('The native login did not identify browser-owner')
  window.scope = ownerScope(location.origin, owner)
  return owner.user
}
window.mount = async (conversationId = 'browser-chat') => {
  window.client = new BrowserClient(window.scope)
  const opened = await window.client.open(conversationId)
  if (opened.state !== 'ready') throw new Error(opened.message)
  window.session = opened.value
  root.render(React.createElement(ShellThemeProvider, { initialPreference: 'light' },
    React.createElement(BrowserControls, { client: window.client, session: opened.value,
      onSessionChange: value => { window.sessionChanges++; window.session = value; window.sessionWaiters.forEach(waiter => waiter(value)) } })))
  return opened.value
}
window.switchOwner = async () => {
  const login = await signInOwner('browser-other', 'other-correct-horse-battery-staple')
  const owner = await readOwnerSession()
  if (login.user !== 'browser-other' || owner.user !== 'browser-other') throw new Error('The native login did not switch owners: ' + owner.user)
  window.scope = ownerScope(location.origin, owner)
  window.client = new BrowserClient(window.scope)
  const foreign = await window.client.get(window.oldSessionId)
  const opened = await window.client.open('dashboard:other-owner')
  if (opened.state !== 'ready') throw new Error(opened.message)
  window.session = opened.value
  root.render(React.createElement(ShellThemeProvider, { initialPreference: 'light' },
    React.createElement(BrowserControls, { client: window.client, session: opened.value,
      onSessionChange: value => { window.sessionChanges++; window.session = value; window.sessionWaiters.forEach(waiter => waiter(value)) } })))
  return { owner: owner.user, foreign, session: opened.value }
}
window.holdNextBrowserGet = () => {
  const original = window.fetch.bind(window)
  let entered
  let release
  const arrived = new Promise(resolve => { entered = resolve })
  const blocked = new Promise(resolve => { release = resolve })
  window.fetch = async (input, init) => {
    const response = await original(input, init)
    const address = typeof input === 'string' ? input : input.url
    if (address.includes('/api/browser/sessions/') && (!init?.method || init.method === 'GET')) {
      window.fetch = original
      entered(response.status)
      await blocked
    }
    return response
  }
  return { arrived, release }
}
window.click = async label => {
  for (let attempt = 0; attempt < 100; attempt++) {
    const button = [...document.querySelectorAll('[aria-label="Browser controls"] button')].find(node => node.textContent === label)
    if (button && !button.disabled) { button.click(); return }
    await new Promise(done => setTimeout(done, 20))
  }
  throw new Error('Missing enabled button: ' + label)
}
window.setField = (label, value) => { const field = document.getElementById('browser-' + label + '-' + window.session.id); const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; setter.call(field, value); field.dispatchEvent(new Event('input', { bubbles: true })) }
window.submit = label => { const field = document.getElementById('browser-' + label + '-' + window.session.id); field.form.requestSubmit() }
window.fixturePost = async (path, body) => { const response = await fetch('/test/browser/' + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); return { status: response.status, body: response.ok ? await response.json() : await response.text() } }
window.fixtureState = async () => await (await fetch('/test/browser/state')).json()
window.loaded = true
` },
      configureServer(server) {
        server.middlewares.use('/integration', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><html><body><div id="root"></div><script type="module" src="/browser-controls-entry.ts"></script></body></html>')
        })
      },
    }],
    server: { host: '127.0.0.1', port, strictPort: true,
      proxy: { '/api': `http://127.0.0.1:${fixture.api_port}`, '/test': `http://127.0.0.1:${fixture.api_port}` } },
  })
  await vite.listen()
}, 30000)

afterAll(async () => {
  socket?.close()
  if (vite) await vite.close()
  for (const child of children) child.kill('SIGTERM')
  for (const directory of directories) await rm(directory, { recursive: true, force: true })
})

async function until(evaluate: (expression: string) => Promise<any>, expression: string, expected: unknown) {
  for (let attempt = 0; attempt < 400; attempt++) {
    if (await evaluate(expression) === expected) return
    await new Promise(done => setTimeout(done, 50))
  }
  throw new Error(`Timed out waiting for ${expression}: ${JSON.stringify(await evaluate(expression))}; state=${JSON.stringify(await evaluate('({ session: window.session, controls: document.querySelector("[aria-label=\\"Browser controls\\"]")?.textContent })'))}`)
}

describe('owned browser controls', () => {
  it('drives authenticated Chromium, versioned ownership, preview and guarded lifecycle', async () => {
    const evaluate = await evaluator(`${origin}/integration`)
    await until(evaluate, 'window.loaded === true', true)
    expect(await evaluate('window.signIn()')).toBe('browser-owner')
    const opened = await evaluate('window.mount()')
    expect(opened).toMatchObject({ status: 'reserved', version: 1 })
    await until(evaluate, '!!document.querySelector("[aria-label=\\"Browser controls\\"]")', true)
    await evaluate('window.click("Connect browser")')
    await until(evaluate, 'window.session.status', 'active')
    expect(await evaluate('window.session.version')).toBe(2)
    await evaluate('window.click("Refresh preview")')
    await until(evaluate, 'document.querySelector("[aria-label=\\"Browser preview\\"]")?.textContent.includes("image bytes")', true)
    expect(await evaluate('document.querySelector("[aria-label=\\"Browser preview\\"]")?.textContent')).toContain('version 2')
    expect(await evaluate('document.querySelector("[aria-label=\\"Browser preview\\"]")?.textContent')).toContain('Gideon has control')
    const preview = await evaluate('(async () => { const result = await window.client.preview(window.session); return { state: result.state, size: result.value?.image.size, type: result.value?.image.type, version: result.value?.version, holder: result.value?.controlHolder, timestamp: result.value?.timestamp } })()')
    expect(preview).toMatchObject({ state: 'ready', type: 'image/png', version: 2, holder: 'assistant' })
    expect(preview.size).toBeGreaterThan(100)
    expect(preview.timestamp).toBeGreaterThan(0)

    const assistant = await evaluate(`window.fixturePost('assistant-action', { session_id: window.session.id, expected_version: 2 })`)
    expect(assistant).toMatchObject({ status: 200, body: { session: { version: 3 } } })
    await evaluate('window.click("Take control")')
    await until(evaluate, 'window.session.version', 3)
    expect(await evaluate('document.querySelector("[aria-label=\\"Browser controls\\"] [role=alert]")?.textContent')).toContain('changed')
    await evaluate('window.click("Take control")')
    await until(evaluate, 'window.session.version', 4)
    expect(await evaluate('window.session.controlHolder')).toBe('customer')
    expect(await evaluate('document.querySelector("[aria-label=\\"Browser controls\\"]")?.textContent')).toContain('You have control')

    await evaluate(`window.setField('url', ${JSON.stringify(fixture.owned_url)}); window.submit('url')`)
    await until(evaluate, 'window.session.version', 5)
    await evaluate('window.click("Refresh preview")')
    await until(evaluate, 'document.querySelector("[aria-label=\\"Browser preview\\"]")?.textContent.includes("Owned browser page")', true)
    expect(await evaluate('document.querySelector("[aria-label=\\"Browser preview\\"]")?.textContent')).toContain('version 5')
    await evaluate('window.click("Scroll down")')
    await until(evaluate, 'window.session.version', 6)
    await evaluate(`window.setField('url', ${JSON.stringify(fixture.blocked_url)}); window.submit('url')`)
    await until(evaluate, 'document.querySelector("[aria-label=\\"Browser controls\\"] [role=alert]")?.textContent.includes("changed")', true)
    expect(await evaluate('window.session.version')).toBe(6)
    const state = await evaluate('window.fixtureState()')
    expect(state.hits).not.toContain('blocked')
    expect(state.events.some((event: any) => event.outcome === 'denied' && event.metadata?.host === '127.0.0.2')).toBe(true)
    await evaluate('window.click("Hand back to Gideon")')
    await until(evaluate, 'window.session.version', 7)
    expect(await evaluate('document.querySelector("[aria-label=\\"Browser controls\\"]")?.textContent')).toContain('Gideon has control')
    expect(await evaluate(`window.fixturePost('kill', { enabled: true })`)).toMatchObject({ status: 200 })
    const stopped = await evaluate('window.fixturePost("assistant-action", { session_id: window.session.id, expected_version: 7 })')
    expect(stopped.status).toBe(409)
    await evaluate(`window.fixturePost('kill', { enabled: false })`)
    await evaluate('window.click("Close browser")')
    await until(evaluate, 'window.session.status', 'closed')
    expect(await evaluate('(async () => (await window.client.preview(window.session)).state)()')).toBe('unavailable')
    await evaluate('window.click("Reopen browser")')
    await until(evaluate, 'window.session.status', 'reserved')
    expect(await evaluate('window.session.id')).toBe(opened.id)
    await evaluate('window.click("Connect browser")')
    await until(evaluate, 'window.session.status', 'active')
    const activeVersion = await evaluate('window.session.version')
    expect(await evaluate('window.fixturePost("disconnect", { session_id: window.session.id })'))
      .toMatchObject({ status: 200, body: { disconnected: true } })
    const disconnected = await evaluate('(async () => { const changed = window.nextSession("error", window.session.version); await window.click("Refresh state"); return changed })()')
    expect(disconnected.status).toBe('error')
    expect(disconnected.version).toBeGreaterThan(activeVersion)
    expect(await evaluate('document.querySelector("[aria-label=\\"Browser controls\\"]")?.textContent')).toContain('Connection unavailable')
  }, 120000)

  it('discards a delayed authenticated response after a real owner and session change', async () => {
    const evaluate = await evaluator(`${origin}/integration`)
    await until(evaluate, 'window.loaded === true', true)
    expect(await evaluate('window.signIn()')).toBe('browser-owner')
    const original = await evaluate('window.mount("channel-thread")')
    await until(evaluate, '!!document.querySelector("[aria-label=\\"Browser controls\\"]")', true)
    await evaluate('window.click("Connect browser")')
    await until(evaluate, 'window.session.status', 'active')
    await evaluate('window.click("Take control")')
    await until(evaluate, 'window.session.controlHolder', 'customer')
    await evaluate(`window.setField('url', ${JSON.stringify(fixture.owned_url)}); window.setField('text', 'old-owner draft')`)
    const oldOwnerState = await evaluate('window.session')
    await evaluate('window.oldSessionId = window.session.id')

    await evaluate('window.held = window.holdNextBrowserGet(); true')
    await evaluate('window.click("Refresh state")')
    expect(await evaluate('window.held.arrived')).toBe(200)
    await rotateOwner('browser-other')
    const switched = await evaluate('window.switchOwner()')
    expect(switched.owner).toBe('browser-other')
    expect(switched.foreign).toMatchObject({ state: 'missing' })
    const next = switched.session
    expect(next.id).not.toBe(original.id)
    expect(next.id).not.toBe(oldOwnerState.id)
    expect(await evaluate('window.scope.ownerId')).toBe('browser-other')
    await until(evaluate, 'document.querySelector("[aria-label=\\"Browser controls\\"] [role=status]")?.textContent.includes("Ready to connect")', true)
    expect(await evaluate('document.getElementById("browser-text-" + window.session.id)')).toBeNull()
    const changesBeforeRelease = await evaluate('window.sessionChanges')
    await evaluate('window.held.release()')
    await new Promise(done => setTimeout(done, 250))
    expect(await evaluate('window.sessionChanges')).toBe(changesBeforeRelease)
    expect(await evaluate('window.session.id')).toBe(next.id)
    expect(await evaluate('document.querySelector("[aria-label=\\"Browser controls\\"] [role=status]")?.textContent'))
      .toContain(`version ${next.version}`)
    expect(await evaluate('document.querySelector("[aria-label=\\"Browser controls\\"] [role=alert]")?.textContent')).toBeUndefined()
    expect(await evaluate('document.querySelector("[aria-label=\\"Browser controls\\"]")?.textContent')).not.toContain('old-owner draft')
  }, 90000)
})
