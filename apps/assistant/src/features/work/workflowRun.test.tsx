import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer, type ViteDevServer } from 'vite'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { startBrowserHarness, type BrowserHarness } from '../../../test-support/browserHarness'
import { timelineEvents, unresolvedReviewMessage, type ReviewIntent } from './workflowRunState'

let native: ChildProcessWithoutNullStreams | undefined
let profile = ''
let viteCache = ''
let vite: ViteDevServer | undefined
let api = ''
let credential = ''
let runId = ''
let browser: BrowserHarness | undefined
let address = ''

async function port() {
  const server = createNetServer()
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  const value = server.address()
  const number = typeof value === 'object' && value ? value.port : 0
  await new Promise<void>(done => server.close(() => done()))
  return number
}

async function evaluate<T = unknown>(expression: string): Promise<T> {
  if (!browser) throw new Error('Chromium harness is unavailable')
  return browser.evaluate<T>(expression)
}

async function waitFor(expression: string) {
  if (!browser) throw new Error('Chromium harness is unavailable')
  try {
    await browser.waitFor(expression, `workflow run: ${expression}`, 9000)
  } catch (error) {
    const page = await browser.evaluate(`JSON.stringify({ url: location.href, readyState: document.readyState, loaded: window.loaded, title: document.title, body: document.body?.innerText?.slice(0, 3000), currentRoute: window.currentRoute, scripts: Array.from(document.scripts, script => script.src), errors: window.__gideonErrors ?? [] })`).catch(value => String(value))
    throw new Error(`${error instanceof Error ? error.message : String(error)}\nFirst-failure page diagnostics: ${page}\nBrowser diagnostics: ${browser.diagnostics().join('\n')}`)
  }
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
  browser = await startBrowserHarness({ profileDirectory: profile, windowSize: { width: 1280, height: 900 } })
  await browser.navigate(address)
}, 30000)

afterAll(async () => {
  await browser?.close(); if (vite) await vite.close(); native?.kill('SIGTERM')
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
    await waitFor(`document.querySelector('[aria-label="Workflow run catalogue"]')?.textContent.includes('assistant-workflow')`)
    expect(await fetch(`${api}/api/workflows/runs/${runId}`, { headers: { Authorization: `Bearer ${credential}` } }).then(response => response.ok)).toBe(true)
    await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent.includes('${runId}')).click()`)
    await waitFor(`document.querySelector('[aria-label="Run timeline"]')?.textContent.includes('${runId}')`)
    expect(await evaluate(`document.querySelector('[aria-label="Run timeline"]')?.textContent.includes('root.children[0]')`)).toBe(true)
    const route = JSON.parse(await evaluate('JSON.stringify(window.currentRoute)'))
    expect(route).toMatchObject({ placement: { id: 'workflows/run' }, record: { kind: 'workflow_run', id: runId }, returnTo: { destination: 'chat', sessionId: 'run-source' } })
    await waitFor(`document.querySelector('[aria-label="Exact run review"]')?.textContent.includes('review-success')`)
    await evaluate(`Array.from(document.querySelectorAll('[aria-label="Exact run review"] li')).find(item => item.textContent.includes('review-success'))?.querySelectorAll('button')[1].click()`)
    await waitFor(`document.querySelector('[aria-label="Exact run review"] [role="status"]')?.textContent.includes('Review recorded for run ${runId}')`)
    const firstTriage = await fetch(`${api}/__test/stats`).then(response => response.json()) as { triage_attempts: number; calibration_count: number }
    expect(firstTriage).toMatchObject({ triage_attempts: 1, calibration_count: 1 })
    await evaluate('location.reload()')
    await waitFor(`document.querySelector('[aria-label="Run timeline"]')?.textContent.includes('${runId}')`)
    await waitFor(`document.querySelector('[aria-label="Exact run review"] [role="status"]')?.textContent.includes('Review recorded for run ${runId}')`)
    const afterSuccessfulRecovery = await fetch(`${api}/__test/stats`).then(response => response.json()) as { triage_attempts: number; calibration_count: number }
    expect(afterSuccessfulRecovery).toMatchObject({ triage_attempts: 1, calibration_count: 1 })
    await fetch(`${api}/__test/drop-next-triage`, { method: 'POST' })
    await evaluate(`Array.from(document.querySelectorAll('[aria-label="Exact run review"] li')).find(item => item.textContent.includes('review-ambiguous'))?.querySelectorAll('button')[1].click()`)
    await waitFor(`document.querySelector('[aria-label="Exact run review"] [role="alert"]')?.textContent.includes('will not be replayed automatically')`)
    await evaluate('location.reload()')
    await waitFor(`document.querySelector('[aria-label="Exact run review"] [role="alert"]')?.textContent.includes('will not be replayed automatically')`)
    const afterAmbiguousRecovery = await fetch(`${api}/__test/stats`).then(response => response.json()) as { triage_attempts: number; calibration_count: number }
    expect(afterAmbiguousRecovery).toMatchObject({ triage_attempts: 2, calibration_count: 2 })
    expect(await evaluate(`Array.from(document.querySelectorAll('[aria-label="Exact run review"] li')).find(item => item.textContent.includes('review-ambiguous'))?.querySelectorAll('button')[1].disabled`)).toBe(true)
    await evaluate(`document.querySelector('[aria-label="Back"]').click()`)
    await waitFor('window.currentRoute?.destination === "chat" && window.currentRoute?.sessionId === "run-source"')
  }, 30000)
})
