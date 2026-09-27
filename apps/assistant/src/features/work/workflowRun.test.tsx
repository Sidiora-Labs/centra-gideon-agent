import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer, type ViteDevServer } from 'vite'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { timelineEvents, unresolvedReviewMessage, type ReviewIntent } from './workflowRunState'

let native: ChildProcessWithoutNullStreams | undefined
let chromium: ChildProcessWithoutNullStreams | undefined
let profile = ''
let viteCache = ''
let vite: ViteDevServer | undefined
let api = ''
let credential = ''
let runId = ''
let browserSocket: WebSocket | undefined
let evaluate: (expression: string) => Promise<any>
let address = ''

async function port() {
  const server = createNetServer()
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  const value = server.address()
  const number = typeof value === 'object' && value ? value.port : 0
  await new Promise<void>(done => server.close(() => done()))
  return number
}

async function waitFor(expression: string) {
  for (let index = 0; index < 120; index++) {
    if (await evaluate(expression)) return
    await new Promise(done => setTimeout(done, 75))
  }
  throw new Error(`Browser condition did not become true: ${expression}`)
}

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  native = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', [join(root, 'apps/assistant/test-support/workflow_server.py')], {
    env: { ...process.env, PYTHONPATH: join(root, 'runtime') },
  })
  const startup = await new Promise<string>((done, reject) => {
    let output = ''; let errors = ''
    const timer = setTimeout(() => reject(new Error(`Workflow server timed out: ${errors}`)), 15000)
    native!.stdout.on('data', chunk => { output += String(chunk); if (output.includes('\n')) { clearTimeout(timer); done(output.split('\n')[0]) } })
    native!.stderr.on('data', chunk => { errors += String(chunk) })
    native!.once('exit', code => { clearTimeout(timer); reject(new Error(`Workflow server exited ${code}: ${errors}`)) })
  })
  const ready = JSON.parse(startup) as { port: number; credential: string; run_id: string }
  api = `http://127.0.0.1:${ready.port}`; credential = ready.credential; runId = ready.run_id
  const webPort = await port(); address = `http://127.0.0.1:${webPort}/integration`
  viteCache = await mkdtemp(join(tmpdir(), 'gideon-workflow-run-vite-'))
  vite = await createServer({
    configFile: false, root: join(root, 'apps/assistant'), cacheDir: join(viteCache, 'cache'),
    resolve: { alias: [{ find: /^react-native$/, replacement: 'react-native-web' }] },
    optimizeDeps: { include: ['react', 'react-dom/client', 'react-native-web'] },
    plugins: [{
      name: 'native-workflow-run',
      resolveId(id) { if (id === '/run-entry.ts') return '\0run-entry' },
      load(id) { if (id === '\0run-entry') return `
import React from 'react'
import { createRoot } from 'react-dom/client'
import WorkRoutes, { createWorkRoute } from '/src/features/work/WorkRoutes.web.tsx'
import { parseShellRoute, serializeShellRoute } from '/src/shared/shell/shellRoutes.ts'
const root = createRoot(document.getElementById('root'))
const scope = { runtimeOrigin: location.origin, ownerId: 'workflow-owner', cacheKey: 'workflow-owner' }
const fromUrl = () => { const shell = new URL(location.href).searchParams.get('shell'); if (!shell) return createWorkRoute('workflows/run', undefined, { destination: 'chat', sessionId: 'run-source' }); const route = parseShellRoute(new URL(shell, location.origin), location.origin); return route.kind === 'route' ? route : createWorkRoute('workflows/run') }
const render = route => { window.currentRoute = route; root.render(React.createElement(WorkRoutes, { scope, route, navigate: next => { history.pushState({}, '', location.pathname + '?shell=' + encodeURIComponent(serializeShellRoute(next))); render(next) } })) }
render(fromUrl()); window.loaded = true
` },
      configureServer(server) { server.middlewares.use('/integration', (_request, response) => { response.setHeader('Content-Type', 'text/html; charset=utf-8'); response.end('<!doctype html><html><body><div id="root"></div><script type="module" src="/run-entry.ts"></script></body></html>') }) },
    }],
    server: { host: '127.0.0.1', port: webPort, strictPort: true, proxy: { '/api': { target: api, changeOrigin: true, headers: { Authorization: `Bearer ${credential}` } } } },
  })
  await vite.listen()
  profile = await mkdtemp(join(tmpdir(), 'gideon-workflow-run-browser-'))
  const debug = await port()
  chromium = spawn(process.env.CHROMIUM_BIN || 'chromium', ['--headless', '--no-sandbox', '--disable-gpu', '--disable-background-networking',
    `--remote-debugging-port=${debug}`, `--user-data-dir=${profile}`, 'about:blank'])
  let target: { webSocketDebuggerUrl: string } | undefined
  for (let index = 0; index < 100 && !target; index++) {
    try { const pages = await (await fetch(`http://127.0.0.1:${debug}/json/list`)).json() as Array<{ type: string; webSocketDebuggerUrl: string }>; target = pages.find(page => page.type === 'page') } catch {}
    if (!target) await new Promise(done => setTimeout(done, 100))
  }
  if (!target) throw new Error('Chromium did not expose a page')
  browserSocket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise<void>((done, reject) => { browserSocket!.addEventListener('open', () => done(), { once: true }); browserSocket!.addEventListener('error', reject, { once: true }) })
  let next = 0
  const pending = new Map<number, { resolve(value: unknown): void; reject(error: Error): void }>()
  browserSocket.addEventListener('message', event => { const response = JSON.parse(String(event.data)); if (!response.id) return; const item = pending.get(response.id); if (!item) return; pending.delete(response.id); response.error ? item.reject(new Error(response.error.message)) : item.resolve(response.result) })
  const command = (method: string, params: Record<string, unknown> = {}) => new Promise<any>((done, reject) => { const id = ++next; pending.set(id, { resolve: done, reject }); browserSocket!.send(JSON.stringify({ id, method, params })) })
  await command('Page.enable'); await command('Runtime.enable'); await command('Page.navigate', { url: address })
  evaluate = async expression => { const response = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }); if (response.result?.exceptionDetails) throw new Error(response.result.exceptionDetails.exception?.description ?? 'Browser evaluation failed'); return response.result?.result?.value }
}, 30000)

afterAll(async () => {
  browserSocket?.close(); chromium?.kill('SIGTERM'); if (vite) await vite.close(); native?.kill('SIGTERM')
  for (const directory of [profile, viteCache]) if (directory) await rm(directory, { recursive: true, force: true })
})

describe('native workflow run workspace', () => {
  it('retains native IDs in the timeline and review intent recovery stays truthful', () => {
    const events = timelineEvents('run-native-1', { status: 'complete', nodes: [{ node_id: 'stage-a', instance_path: 'root.children[0]', state: 'done', attempt: 1 }] })
    expect(events.map(row => row.id)).toEqual(['run-native-1', 'run-native-1:root.children[0]:stage-a'])
    expect(events[1].href).toContain('/run-native-1/outputs/stage-a')
    const unknown: ReviewIntent = { runId: 'run-native-1', decisions: [{ key: 'finding-1', outcome: 'accept' }], state: 'unknown', updatedAt: 3 }
    expect(unresolvedReviewMessage(unknown, null)).toContain('will not be replayed automatically')
  })

  it('opens the real native run from the Work catalogue, reloads it, and returns to its source', async () => {
    await waitFor(`window.loaded === true && !!document.querySelector('[aria-label="Workflow run catalogue"]')`)
    expect(await fetch(`${api}/api/workflows/runs/${runId}`, { headers: { Authorization: `Bearer ${credential}` } }).then(response => response.ok)).toBe(true)
    expect(await evaluate('document.body.textContent.includes("assistant-workflow")')).toBe(true)
    await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent.includes('${runId}')).click()`)
    await waitFor(`document.querySelector('[aria-label="Run timeline"]')?.textContent.includes('${runId}')`)
    expect(await evaluate(`document.querySelector('[aria-label="Run timeline"]')?.textContent.includes('root.children[0]')`)).toBe(true)
    const route = JSON.parse(await evaluate('JSON.stringify(window.currentRoute)'))
    expect(route).toMatchObject({ placement: { id: 'workflows/run' }, record: { kind: 'workflow_run', id: runId }, returnTo: { destination: 'chat', sessionId: 'run-source' } })
    await evaluate('location.reload()')
    await waitFor(`document.querySelector('[aria-label="Run timeline"]')?.textContent.includes('${runId}')`)
    await evaluate(`document.querySelector('[aria-label="Back"]').click()`)
    await waitFor('window.currentRoute?.destination === "chat" && window.currentRoute?.sessionId === "run-source"')
  }, 30000)
})
