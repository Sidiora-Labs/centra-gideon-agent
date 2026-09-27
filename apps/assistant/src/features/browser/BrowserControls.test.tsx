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
    const timeout = setTimeout(() => reject(new Error(`Browser server timed out: ${errors}`)), 20000)
    server.stdout.on('data', chunk => {
      output += String(chunk)
      if (output.includes('\n')) { clearTimeout(timeout); done(JSON.parse(output.split('\n')[0])) }
    })
    server.stderr.on('data', chunk => { errors += String(chunk) })
    server.once('exit', code => { clearTimeout(timeout); reject(new Error(`Browser server exited ${code}: ${errors}`)) })
  })
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
      name: 'browser-controls-entry',
      resolveId(id) { if (id === '/browser-controls-entry.ts') return '\0browser-controls-entry' },
      load(id) { if (id === '\0browser-controls-entry') return `
import React from 'react'
import { createRoot } from 'react-dom/client'
import BrowserControls from '/src/features/browser/BrowserControls.web.tsx'
import { BrowserClient } from '/src/features/browser/browserClient.ts'
import { ownerScope, signInOwner } from '/src/shared/auth.web.tsx'
import { ShellThemeProvider } from '/src/shared/shell/shellTheme.web.ts'
const root = createRoot(document.getElementById('root'))
window.signIn = async () => { window.scope = ownerScope(location.origin, await signInOwner('browser-owner', 'correct-horse-battery-staple')); return window.scope.ownerId }
window.mount = async () => {
  window.client = new BrowserClient(window.scope)
  const opened = await window.client.open('browser-chat')
  if (opened.state !== 'ready') throw new Error(opened.message)
  window.session = opened.value
  root.render(React.createElement(ShellThemeProvider, { initialPreference: 'light' },
    React.createElement(BrowserControls, { client: window.client, session: opened.value,
      onSessionChange: value => { window.session = value } })))
  return opened.value
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
    await evaluate('window.fixturePost("disconnect", { session_id: window.session.id })')
    await evaluate('window.click("Refresh state")')
    await until(evaluate, 'window.session.status', 'error')
    expect(await evaluate('document.querySelector("[aria-label=\\"Browser controls\\"]")?.textContent')).toContain('Connection unavailable')
  }, 120000)
})
