import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer, type ViteDevServer } from 'vite'
import { afterAll, describe, expect, it } from 'vitest'

const root = resolve(process.cwd(), '../..')
const password = 'correct-horse-battery-staple'
const processes: ChildProcessWithoutNullStreams[] = []
const directories: string[] = []
let vite: ViteDevServer | undefined
let browser: WebSocket | undefined

afterAll(async () => {
  browser?.close()
  for (const process of processes) process.kill('SIGTERM')
  if (vite) await vite.close()
  for (const directory of directories) await rm(directory, { recursive: true, force: true })
})

async function availablePort(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(resolveReady => server.listen(0, '127.0.0.1', resolveReady))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(resolveClosed => server.close(() => resolveClosed()))
  return port
}

async function authServer(origin: string): Promise<{ api: string; control: string }> {
  const command = process.env.GIDEON_TEST_PYTHON || 'python3'
  const child = spawn(command, [join(root, 'apps/assistant/test-support/auth_server.py'), origin], {
    env: { ...process.env, PYTHONPATH: join(root, 'runtime') },
  })
  processes.push(child)
  const line = await new Promise<string>((resolveLine, rejectLine) => {
    let output = ''
    let errors = ''
    const timeout = setTimeout(() => rejectLine(new Error(`Auth server start timed out: ${errors}`)), 15000)
    child.stdout.on('data', chunk => {
      output += String(chunk)
      if (output.includes('\n')) {
        clearTimeout(timeout)
        resolveLine(output.split('\n')[0])
      }
    })
    child.stderr.on('data', chunk => { errors += String(chunk) })
    child.once('exit', code => {
      clearTimeout(timeout)
      rejectLine(new Error(`Auth server exited ${code}: ${errors}`))
    })
  })
  const ports = JSON.parse(line) as { api_port: number; control_port: number }
  return { api: `http://127.0.0.1:${ports.api_port}`, control: `http://127.0.0.1:${ports.control_port}` }
}

const browserEntry = `
import React from 'react'
import { createRoot } from 'react-dom/client'
import { AssistantBootstrapProvider, useAssistantBootstrap } from '/src/shared/bootstrap.web.tsx'
import { signInOwner } from '/src/shared/auth.web.tsx'
window.__auth = { signInOwner }
window.__cleared = []
function Ready() {
  const bootstrap = useAssistantBootstrap()
  React.useEffect(() => {
    window.__bootstrap = bootstrap
    window.__state = bootstrap.state
  }, [bootstrap])
  return React.createElement('p', { id: 'owner-ready' }, bootstrap.state.owner?.user)
}
createRoot(document.getElementById('root')).render(
  React.createElement(AssistantBootstrapProvider, {
    clearOwnerCache: scope => window.__cleared.push(scope.cacheKey),
  }, React.createElement(Ready))
)
`

async function startVite(port: number, api: string): Promise<void> {
  vite = await createServer({
    configFile: false,
    root: join(root, 'apps/assistant'),
    plugins: [{
      name: 'assistant-auth-integration',
      resolveId(id) { if (id === '/integration-entry.tsx') return '\0integration-entry' },
      load(id) { if (id === '\0integration-entry') return browserEntry },
      configureServer(server) {
        server.middlewares.use('/integration', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><html><body><div id="root"></div><script type="module" src="/integration-entry.tsx"></script></body></html>')
        })
      },
    }],
    server: { host: '127.0.0.1', port, strictPort: true, proxy: { '/api': api } },
  })
  await vite.listen()
}

type Pending = { resolve: (value: any) => void; reject: (error: Error) => void }

async function startBrowser(): Promise<(method: string, params?: Record<string, unknown>) => Promise<any>> {
  const directory = await mkdtemp(join(tmpdir(), 'gideon-assistant-browser-'))
  directories.push(directory)
  const debuggingPort = await availablePort()
  const child = spawn(process.env.CHROMIUM_BIN || 'chromium', [
    '--headless', '--no-sandbox', '--disable-gpu', '--disable-background-networking',
    `--remote-debugging-port=${debuggingPort}`, `--user-data-dir=${directory}`, 'about:blank',
  ])
  processes.push(child)
  let targets: Array<{ type: string; webSocketDebuggerUrl: string }> | undefined
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      targets = await (await fetch(`http://127.0.0.1:${debuggingPort}/json`)).json()
      break
    } catch { await new Promise(done => setTimeout(done, 100)) }
  }
  if (!targets) throw new Error('Chromium did not expose a debugging port')
  const target = targets.find(item => item.type === 'page')
  if (!target) throw new Error('Chromium did not open a page target')
  browser = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise<void>((resolveOpen, rejectOpen) => {
    browser!.addEventListener('open', () => resolveOpen(), { once: true })
    browser!.addEventListener('error', () => rejectOpen(new Error('Chromium debugger connection failed')), { once: true })
  })
  let nextId = 0
  const pending = new Map<number, Pending>()
  browser.addEventListener('message', event => {
    const message = JSON.parse(String(event.data)) as { id?: number; result?: any; error?: { message: string } }
    if (message.id === undefined) return
    const waiting = pending.get(message.id)
    if (!waiting) return
    pending.delete(message.id)
    if (message.error) waiting.reject(new Error(message.error.message))
    else waiting.resolve(message.result)
  })
  return (method, params = {}) => new Promise((resolveResult, rejectResult) => {
    const id = ++nextId
    pending.set(id, { resolve: resolveResult, reject: rejectResult })
    browser!.send(JSON.stringify({ id, method, params }))
  })
}

async function evaluate(send: (method: string, params?: Record<string, unknown>) => Promise<any>, expression: string): Promise<any> {
  const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'Browser evaluation failed')
  return result.result?.value
}

async function waitFor(send: (method: string, params?: Record<string, unknown>) => Promise<any>, condition: string): Promise<any> {
  return evaluate(send, `(async()=>{for(let n=0;n<100;n++){const value=(${condition});if(value)return value;await new Promise(resolve=>setTimeout(resolve,100))}throw Error('Timed out waiting for browser state')})()`)
}

describe('real OSS assistant authentication', () => {
  it('keeps browser identity, TOTP, local token and cache transitions owner scoped', async () => {
    const port = await availablePort()
    const origin = `http://127.0.0.1:${port}`
    const server = await authServer(origin)
    await startVite(port, server.api)
    const send = await startBrowser()
    await send('Page.enable')
    await send('Runtime.enable')
    await send('Page.navigate', { url: `${origin}/integration` })
    await waitFor(send, `document.querySelector('#gideon-password') && !document.querySelector('#owner-ready')`)

    const login = async (user: string, code = '') => evaluate(send,
      `window.__auth.signInOwner(${JSON.stringify(user)},${JSON.stringify(password)},${JSON.stringify(code)}).then(owner=>owner.user)`)
    const refresh = async () => evaluate(send, `window.__bootstrap.refresh().then(()=>window.__state)`)
    const mode = async (name: string) => {
      const response = await fetch(`${server.control}/mode`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: name }) })
      expect(response.ok).toBe(true)
    }

    await evaluate(send, `(()=>{
      const setValue=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};
      setValue('gideon-username','owner-a');setValue('gideon-password',${JSON.stringify(password)});
      document.querySelector('form').requestSubmit();return true
    })()`)
    const first = await waitFor(send, `document.querySelector('#owner-ready')?.textContent === 'owner-a' && window.__state?.phase === 'ready' && window.__state.scope`)
    expect(first.ownerId).toBe('owner-a')
    expect(first.runtimeOrigin).toBe(origin)

    await mode('other')
    expect(await login('owner-b')).toBe('owner-b')
    await refresh()
    const switched = await waitFor(send, `document.querySelector('#owner-ready')?.textContent === 'owner-b' && window.__state?.phase === 'ready' && window.__state.scope.ownerId === 'owner-b' && window.__state.scope`)
    expect(switched.ownerId).toBe('owner-b')
    expect(switched.cacheKey).not.toBe(first.cacheKey)
    expect(await evaluate(send, `window.__cleared`)).toContain(first.cacheKey)

    await evaluate(send, `window.__bootstrap.signOut()`)
    expect(await waitFor(send, `!!document.querySelector('#gideon-password') && !document.querySelector('#owner-ready')`)).toBe(true)
    expect(await evaluate(send, `window.__cleared`)).toContain(switched.cacheKey)

    await mode('totp')
    const code = (await (await fetch(`${server.control}/code`)).json() as { code: string }).code
    const refused = await evaluate(send, `window.__auth.signInOwner('owner-a',${JSON.stringify(password)},'').then(()=>'',error=>error.code)`)
    expect(refused).toBe('auth_totp_required')
    expect(await login('owner-a', code)).toBe('owner-a')
    await evaluate(send, `document.querySelector('button[type="button"]').click()`)
    const totpReady = await waitFor(send, `document.querySelector('#owner-ready')?.textContent === 'owner-a' && window.__state?.phase === 'ready' && window.__state.scope`)
    expect(totpReady.ownerId).toBe('owner-a')

    await fetch(`${server.control}/revoke`, { method: 'POST' })
    await refresh()
    expect(await waitFor(send, `!!document.querySelector('#gideon-password') && !document.querySelector('#owner-ready')`)).toBe(true)
    expect(await evaluate(send, `window.__cleared`)).toContain(totpReady.cacheKey)

    await mode('local')
    const token = (await (await fetch(`${server.control}/token`)).json() as { token: string }).token
    const paired = await evaluate(send, `fetch('/api/auth/session?token='+encodeURIComponent(${JSON.stringify(token)}),{credentials:'same-origin',headers:{'X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'}}).then(response=>response.ok)`)
    expect(paired).toBe(true)
    await evaluate(send, `document.querySelector('button[type="button"]').click()`)
    const localReady = await waitFor(send, `document.querySelector('#owner-ready')?.textContent === 'owner-local' && window.__state?.phase === 'ready' && window.__state.scope`)
    expect(localReady.ownerId).toBe('owner-local')
    expect(localReady.runtimeOrigin).toBe(origin)
    await evaluate(send, `window.__bootstrap.signOut()`)
    expect(await evaluate(send, `window.__cleared`)).toContain(localReady.cacheKey)
  }, 90000)
})
