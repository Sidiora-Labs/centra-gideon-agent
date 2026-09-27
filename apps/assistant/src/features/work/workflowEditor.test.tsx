import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer, type ViteDevServer } from 'vite'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

let native: ChildProcessWithoutNullStreams | undefined
let chromium: ChildProcessWithoutNullStreams | undefined
let profile = ''
let viteCache = ''
let socket: WebSocket | undefined
let vite: ViteDevServer | undefined
let api = ''
let credential = ''
let workflowName = ''
let editorUrl = ''
let evaluate: (expression: string) => Promise<any>
let pressEnter: () => Promise<void>
let resetEditor: () => Promise<void>

const nativeFetch = (path: string, init: RequestInit = {}) => fetch(`${api}${path}`, {
  ...init, headers: { Authorization: `Bearer ${credential}`, ...init.headers },
})
const saveDefinition = (body: Record<string, unknown>) => nativeFetch('/api/workflows', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})

async function port(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  const address = server.address()
  const result = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(done => server.close(() => done()))
  return result
}

async function waitFor(expression: string) {
  for (let attempt = 0; attempt < 120; attempt++) {
    if (await evaluate(expression)) return
    await new Promise(done => setTimeout(done, 75))
  }
  throw new Error(`Browser condition did not become true: ${expression}`)
}

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  native = spawn(process.env.GIDEON_TEST_PYTHON || 'python3',
    [join(root, 'apps/assistant/test-support/workflow_server.py')], {
      env: { ...process.env, PYTHONPATH: join(root, 'runtime') },
    })
  const line = await new Promise<string>((done, reject) => {
    let output = ''; let errors = ''
    const timer = setTimeout(() => reject(new Error(`Native workflow server timed out: ${errors}`)), 15000)
    native!.stdout.on('data', chunk => {
      output += String(chunk)
      if (output.includes('\n')) { clearTimeout(timer); done(output.split('\n')[0]) }
    })
    native!.stderr.on('data', chunk => { errors += String(chunk) })
    native!.once('exit', code => { clearTimeout(timer); reject(new Error(`Native workflow server exited ${code}: ${errors}`)) })
  })
  const ready = JSON.parse(line) as { port: number; credential: string; workflow_name: string }
  api = `http://127.0.0.1:${ready.port}`; credential = ready.credential; workflowName = ready.workflow_name
  const webPort = await port()
  editorUrl = `http://127.0.0.1:${webPort}/integration`
  viteCache = await mkdtemp(join(tmpdir(), 'gideon-workflow-vite-'))
  vite = await createServer({
    configFile: false, root: join(root, 'apps/assistant'),
    cacheDir: join(viteCache, 'cache'),
    resolve: { alias: [{ find: /^react-native$/, replacement: 'react-native-web' }] },
    optimizeDeps: { include: ['react', 'react-dom/client', 'react-native-web'] },
    plugins: [{
      name: 'native-workflow-editor',
      resolveId(id) { if (id === '/workflow-entry.ts') return '\0workflow-entry' },
      load(id) { if (id === '\0workflow-entry') return `
import React from 'react'
import { createRoot } from 'react-dom/client'
import WorkflowEditor from '/src/features/work/WorkflowEditor.web.tsx'
const root = createRoot(document.getElementById('root'))
let scope = { runtimeOrigin: location.origin, ownerId: 'native-owner', cacheKey: 'native-owner' }
window.renderEditor = () => root.render(React.createElement(WorkflowEditor, { scope }))
window.switchOwner = () => { scope = { ...scope, ownerId: 'other-owner', cacheKey: 'other-owner' }; window.renderEditor() }
window.loaded = true
` },
      configureServer(server) {
        server.middlewares.use('/integration', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><html><body><div id="root"></div><script type="module" src="/workflow-entry.ts"></script></body></html>')
        })
      },
    }],
    server: { host: '127.0.0.1', port: webPort, strictPort: true, proxy: {
      '/api': { target: api, changeOrigin: true, headers: { Authorization: `Bearer ${credential}` } },
    } },
  })
  await vite.listen()
  profile = await mkdtemp(join(tmpdir(), 'gideon-workflow-editor-'))
  const debugPort = await port()
  chromium = spawn(process.env.CHROMIUM_BIN || 'chromium', [
    '--headless', '--no-sandbox', '--disable-gpu', '--disable-background-networking',
    `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`, 'about:blank',
  ])
  let target: { webSocketDebuggerUrl: string } | undefined
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const rows = await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json() as
        Array<{ type: string; webSocketDebuggerUrl: string }>
      target = rows.find(row => row.type === 'page')
      if (target) break
    } catch { await new Promise(done => setTimeout(done, 100)) }
  }
  if (!target) throw new Error('Chromium did not start')
  socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise<void>((done, reject) => {
    socket!.addEventListener('open', () => done(), { once: true })
    socket!.addEventListener('error', () => reject(new Error('Chromium debugger failed')), { once: true })
  })
  let next = 0
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
    const id = ++next; pending.set(id, { done, reject }); socket!.send(JSON.stringify({ id, method, params }))
  })
  pressEnter = async () => {
    await command('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 })
    await command('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 })
  }
  await command('Page.navigate', { url: editorUrl })
  evaluate = async expression => {
    let response: { result?: { value?: any }; exceptionDetails?: { text?: string; exception?: { description?: string } } }
    try {
      response = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    } catch (error) { throw new Error(`${error instanceof Error ? error.message : String(error)} at ${expression}`) }
    if (response.exceptionDetails) throw new Error(`${response.exceptionDetails.exception?.description || response.exceptionDetails.text} at ${expression}`)
    return response.result?.value
  }
  await waitFor('window.loaded === true')
  await waitFor('typeof window.renderEditor === "function"')
  resetEditor = async () => {
    await command('Page.navigate', { url: editorUrl })
    await waitFor('document.readyState === "complete" && window.loaded === true')
    await waitFor('typeof window.renderEditor === "function"')
  }
}, 30000)

afterAll(async () => {
  socket?.close(); chromium?.kill('SIGTERM')
  if (vite) await vite.close()
  if (profile) await rm(profile, { recursive: true, force: true })
  if (viteCache) await rm(viteCache, { recursive: true, force: true })
  native?.kill('SIGTERM')
})

describe('native workflow definition editor', () => {
  it('edits and saves the real native DAG with keyboard-selectable ports and revision context', async () => {
    await resetEditor()
    await evaluate('window.renderEditor()')
    await waitFor('!!document.querySelector("[aria-label=\\"Workflow graph\\"]")')
    expect(await evaluate('document.body.textContent.includes("Source revision: 1")')).toBe(true)
    const finish = Array.from(await evaluate('Array.from(document.querySelectorAll("[aria-label=\\"Workflow graph\\"] button"), b => b.textContent)') as string[])
    expect(finish.some(text => text.includes('Input port: seed'))).toBe(true)
    await evaluate(`(() => { const button = Array.from(document.querySelectorAll('[aria-label="Workflow graph"] button')).find(b => b.textContent.includes('Prepare input')); button.focus(); return document.activeElement === button })()`)
    expect(await evaluate('document.activeElement?.textContent.includes("Prepare input")')).toBe(true)
    await pressEnter()
    await evaluate(`Array.from(document.querySelectorAll('[aria-label="Workflow graph"] button')).find(b => b.textContent.includes('Prepare input')).click()`)
    await waitFor('document.querySelector("[aria-label=\\"Workflow node inspector\\"]")?.textContent.includes("Selected transform node · seed")')
    await evaluate(`(() => { const input = document.querySelector('[aria-label="Node label"]'); const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; setter.call(input, 'Prepared input'); input.dispatchEvent(new Event('input', { bubbles: true })) })()`)
    await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Save workflow').click()`)
    await waitFor('document.body.textContent.includes("Saved assistant-workflow as version 2")')
    const detail = await (await nativeFetch(`/api/workflows/${encodeURIComponent(workflowName)}`)).json() as
      { definition: { version: number; root: { children: Array<{ id: string; label: string }> } } }
    expect(detail.definition.version).toBe(2)
    expect(detail.definition.root.children.find(node => node.id === 'seed')?.label).toBe('Prepared input')
  }, 30000)

  it('retains a draft on a changed revision and reports native concurrent-write conflict', async () => {
    await resetEditor()
    await evaluate('window.renderEditor()')
    await waitFor('document.querySelector("[aria-label=\\"Workflow graph\\"]")?.textContent.includes("Prepared input")')
    await evaluate(`(() => { const input = document.querySelector('[aria-label="Workflow description"]'); const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set; setter.call(input, 'Local draft must survive'); input.dispatchEvent(new Event('input', { bubbles: true })) })()`)
    const current = await (await nativeFetch(`/api/workflows/${encodeURIComponent(workflowName)}`)).json() as
      { definition: Record<string, any> }
    const external = await saveDefinition({ ...current.definition, description: 'External edit', save: true,
      expected_revision: current.definition.version })
    expect(external.status).toBe(201)
    await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Save workflow').click()`)
    await waitFor('document.querySelector("[role=alert]")?.textContent.includes("Revision conflict")')
    expect(await evaluate('document.querySelector("[aria-label=\\"Workflow description\\"]")?.value')).toBe('Local draft must survive')

    const refreshed = await (await nativeFetch(`/api/workflows/${encodeURIComponent(workflowName)}`)).json() as
      { definition: Record<string, any> }
    const revision = refreshed.definition.version as number
    const writes = await Promise.all([
      saveDefinition({ ...refreshed.definition, description: 'Concurrent writer A', save: true, expected_revision: revision }),
      saveDefinition({ ...refreshed.definition, description: 'Concurrent writer B', save: true, expected_revision: revision }),
    ])
    expect(writes.map(response => response.status).sort()).toEqual([201, 409])
    const now = await (await nativeFetch(`/api/workflows/${encodeURIComponent(workflowName)}`)).json() as
      { definition: { version: number } }
    expect(now.definition.version).toBe(revision + 1)
  }, 30000)

  it('masks owner state and cancels a stale save after its real revision preflight', async () => {
    await resetEditor()
    await evaluate('window.renderEditor()')
    await waitFor('!!document.querySelector("[aria-label=\\"Workflow graph\\"]")')
    await evaluate(`(() => { const input = document.querySelector('[aria-label="Workflow description"]'); const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set; setter.call(input, 'Owner A draft must not cross scopes'); input.dispatchEvent(new Event('input', { bubbles: true })) })()`)
    await fetch(`${api}/__test/arm`, { method: 'POST' })
    await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Save workflow').click()`)
    expect((await (await fetch(`${api}/__test/wait`)).json() as { started: boolean }).started).toBe(true)
    await evaluate('window.switchOwner()')
    expect(await evaluate('!document.querySelector("[aria-label=\\"Workflow description\\"]") && !document.body.textContent.includes("Owner A draft must not cross scopes")')).toBe(true)
    await fetch(`${api}/__test/release`, { method: 'POST' })
    await new Promise(done => setTimeout(done, 250))
    expect((await (await fetch(`${api}/__test/stats`)).json() as { save_writes: number }).save_writes).toBe(0)
    await waitFor('!!document.querySelector("[aria-label=\\"Workflow graph\\"]")')
    expect(await evaluate('!document.body.textContent.includes("Owner A draft must not cross scopes")')).toBe(true)

    await evaluate(`(() => { const input = document.querySelector('[aria-label="Workflow description"]'); const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set; setter.call(input, 'Selected workflow draft must be discarded'); input.dispatchEvent(new Event('input', { bubbles: true })) })()`)
    await fetch(`${api}/__test/arm`, { method: 'POST' })
    await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Save workflow').click()`)
    expect((await (await fetch(`${api}/__test/wait`)).json() as { started: boolean }).started).toBe(true)
    await evaluate(`(() => { const select = document.querySelector('[aria-label="Workflow definition"]'); const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set; setter.call(select, ''); select.dispatchEvent(new Event('change', { bubbles: true })) })()`)
    await waitFor('!document.querySelector("[aria-label=\\"Workflow description\\"]")')
    await fetch(`${api}/__test/release`, { method: 'POST' })
    await new Promise(done => setTimeout(done, 250))
    expect((await (await fetch(`${api}/__test/stats`)).json() as { save_writes: number }).save_writes).toBe(0)
    expect(await evaluate('!document.body.textContent.includes("Selected workflow draft must be discarded")')).toBe(true)
  }, 30000)

  it('validates, publishes, and sends start through native workflow operations', async () => {
    await resetEditor()
    await evaluate('window.renderEditor()')
    await waitFor('!!document.querySelector("[aria-label=\\"Workflow graph\\"]")')
    await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Validate').click()`)
    await waitFor('document.body.textContent.includes("Gideon validated this workflow")')
    await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Publish workflow').click()`)
    await waitFor('document.body.textContent.includes("Workflow published.")')
    const published = await (await nativeFetch(`/api/workflows/${encodeURIComponent(workflowName)}`)).json() as
      { definition: { metadata?: { a2a_published?: boolean } } }
    expect(published.definition.metadata?.a2a_published).toBe(true)
    await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Start workflow').click()`)
    await waitFor('document.querySelector("[role=alert]")?.textContent.includes("supervisor")')
    expect(await evaluate('document.body.textContent.includes("Workflow started")')).toBe(false)
  }, 30000)
})
