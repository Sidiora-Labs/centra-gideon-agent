import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer, type ViteDevServer } from 'vite'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { startBrowserHarness, type BrowserHarness } from '../../../test-support/browserHarness'

let native: ChildProcessWithoutNullStreams | undefined
let profile = ''
let viteCache = ''
let vite: ViteDevServer | undefined
let api = ''
let credential = ''
let triggerId = ''
let loopId = ''
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
  try { await browser.waitFor(expression, `automation work: ${expression}`, 10000) }
  catch (error) {
    const page = await browser.evaluate(`JSON.stringify({url:location.href,readyState:document.readyState,body:document.body?.innerText?.slice(0,4000),route:window.currentRoute,errors:window.__gideonErrors??[]})`).catch(value => String(value))
    throw new Error(`${error instanceof Error ? error.message : String(error)}\nPage diagnostics: ${page}\nBrowser diagnostics: ${browser.diagnostics().join('\n')}`)
  }
}

async function click(text: string) {
  const found = await evaluate(`(() => { const button = Array.from(document.querySelectorAll('button')).find(candidate => candidate.textContent?.includes(${JSON.stringify(text)})); if (!button) return false; button.click(); return true })()`)
  if (found === false) throw new Error(`Button not found: ${text}`)
}

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  native = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', [join(root, 'apps/assistant/test-support/workflow_server.py')], {
    env: { ...process.env, PYTHONPATH: join(root, 'runtime') },
  })
  const startup = await new Promise<string>((done, reject) => {
    let output = ''; let errors = ''
    const timer = setTimeout(() => reject(new Error(`Automation server timed out: ${errors}`)), 20000)
    native!.stdout.on('data', chunk => { output += String(chunk); if (output.includes('\n')) { clearTimeout(timer); done(output.split('\n')[0]) } })
    native!.stderr.on('data', chunk => { errors += String(chunk) })
    native!.once('exit', code => { clearTimeout(timer); reject(new Error(`Automation server exited ${code}: ${errors}`)) })
  })
  const ready = JSON.parse(startup) as { port: number; credential: string; trigger_id: string; loop_id: string }
  api = `http://127.0.0.1:${ready.port}`; credential = ready.credential; triggerId = ready.trigger_id; loopId = ready.loop_id
  const webPort = await port(); address = `http://127.0.0.1:${webPort}/integration`
  viteCache = await mkdtemp(join(tmpdir(), 'gideon-automation-work-vite-'))
  vite = await createServer({
    configFile: false, root: join(root, 'apps/assistant'), cacheDir: join(viteCache, 'cache'),
    resolve: { alias: [{ find: /^react-native$/, replacement: 'react-native-web' }] },
    optimizeDeps: { include: ['react', 'react-dom/client', 'react-dom', 'react-native-web'] },
    plugins: [{
      name: 'native-automation-work',
      resolveId(id) { if (id === '/automation-entry.ts') return '\0automation-entry' },
      load(id) { if (id === '\0automation-entry') return `
import React from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import WorkRoutes, { createWorkRoute } from '/src/features/work/WorkRoutes.web.tsx'
import { parseShellRoute, serializeShellRoute } from '/src/shared/shell/shellRoutes.ts'
const root = createRoot(document.getElementById('root'))
let owner = 'automation-owner'
const fromUrl = () => { const shell = new URL(location.href).searchParams.get('shell'); if (!shell) return createWorkRoute('triggers', undefined, { destination: 'chat', sessionId: 'automation-source' }); const route = parseShellRoute(new URL(shell, location.origin), location.origin); return route.kind === 'route' ? route : createWorkRoute('triggers') }
const render = route => { window.currentRoute = route; window.currentOwner = owner; root.render(React.createElement(WorkRoutes, { scope: { runtimeOrigin: location.origin, ownerId: owner, cacheKey: owner }, route, navigate: next => { history.pushState({}, '', location.pathname + '?shell=' + encodeURIComponent(serializeShellRoute(next))); render(next) } })) }
render(fromUrl()); window.loaded = true
window.gotoWork = id => { const next = createWorkRoute(id, undefined, window.currentRoute.returnTo); history.pushState({}, '', location.pathname + '?shell=' + encodeURIComponent(serializeShellRoute(next))); render(next) }
window.gotoWorkRecord = (placement, id) => { const next = createWorkRoute(placement, id, window.currentRoute.returnTo); history.pushState({}, '', location.pathname + '?shell=' + encodeURIComponent(serializeShellRoute(next))); render(next) }
window.changeOwner = id => { owner = id; flushSync(() => render(fromUrl())) }
` },
      configureServer(server) { server.middlewares.use('/integration', (_request, response) => { response.setHeader('Content-Type', 'text/html; charset=utf-8'); response.end('<!doctype html><html><body><div id="root"></div><script type="module" src="/automation-entry.ts"></script></body></html>') }) },
    }],
    server: { host: '127.0.0.1', port: webPort, strictPort: true, proxy: { '/api': { target: api, changeOrigin: true, headers: { Authorization: `Bearer ${credential}` } } } },
  })
  await vite.listen()
  profile = await mkdtemp(join(tmpdir(), 'gideon-automation-work-browser-'))
  browser = await startBrowserHarness({ profileDirectory: profile, windowSize: { width: 1280, height: 900 } })
  await browser.navigate(address)
}, 35000)

afterAll(async () => {
  await browser?.close(); if (vite) await vite.close(); native?.kill('SIGTERM')
  for (const directory of [profile, viteCache]) if (directory) await rm(directory, { recursive: true, force: true })
})

describe('native triggers and loops in WorkRoutes', () => {
  it('opens a native event trigger, honors owner changes during a pending write, and returns to its source', async () => {
    await waitFor(`window.loaded === true && !!document.querySelector('[aria-label="Trigger catalogue"]')`)
    await waitFor(`document.querySelector('[aria-label="Trigger catalogue"]')?.textContent.includes('assistant-event-fixture')`)
    await click('assistant-event-fixture')
    await waitFor(`document.querySelector('[aria-label="Trigger readiness"]')?.textContent.includes('${triggerId}')`)
    expect(await evaluate(`window.currentRoute?.placement?.id`)).toBe('triggers')
    expect(await evaluate(`document.querySelector('[aria-label="Trigger run history"]')?.textContent`)).toContain('history')
    await fetch(`${api}/__test/hold-next-native-write`, { method: 'POST' })
    await click('Pause')
    const waiting = await fetch(`${api}/__test/native-write-started`).then(response => response.json()) as { started: boolean }
    expect(waiting.started).toBe(true)
    await fetch(`${api}/__test/hold-trigger-reads`, { method: 'POST' })
    await evaluate(`window.changeOwner('automation-owner-next')`)
    expect(await evaluate(`window.currentOwner`)).toBe('automation-owner-next')
    const readStarted = await fetch(`${api}/__test/trigger-read-started`).then(response => response.json()) as { started: boolean }
    expect(readStarted.started).toBe(true)
    expect(await evaluate(`document.querySelector('main')?.getAttribute('aria-busy') === 'true' && document.querySelector('[aria-label="Trigger readiness"]') === null`)).toBe(true)
    await fetch(`${api}/__test/release-trigger-reads`, { method: 'POST' })
    await waitFor(`document.querySelector('[aria-label="Trigger readiness"]')?.textContent.includes('Status: disabled')`)
    await fetch(`${api}/__test/release-native-write`, { method: 'POST' })
    await waitFor(`document.querySelector('[aria-label="Trigger controls"] button')?.textContent.includes('Enable')`)
    expect(await evaluate(`document.querySelector('[role="alert"]')?.textContent ?? ''`)).toBe('')
    await click('Catalogue')
    await waitFor(`document.querySelector('[aria-label="Trigger catalogue"]')`)
    await click('New automation')
    await waitFor(`document.querySelector('[aria-label="New trigger"]')`)
    const scheduleName = 'Browser schedule fixture'
    await evaluate(`(() => { const node = document.querySelector('[aria-label="New trigger"] input'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(node, ${JSON.stringify(scheduleName)}); node.dispatchEvent(new Event('input', {bubbles:true})) })()`)
    await click('Save automation')
    await waitFor(`window.currentRoute?.placement?.id === 'triggers' && !!window.currentRoute?.record?.id`)
    expect(await evaluate(`window.currentRoute?.record?.id`)).toContain('schedule:')
    await waitFor(`document.querySelector('[aria-label="Trigger readiness"]')?.textContent.includes('Next event:')`)
    await evaluate('location.reload()')
    await waitFor(`document.querySelector('[aria-label="Trigger readiness"]')?.textContent.includes('Next event:')`)
    await click('Back')
    await waitFor(`window.currentRoute?.destination === 'chat' && window.currentRoute?.sessionId === 'automation-source'`)
  }, 30000)

  it('shows a real missing trigger and distinguishes failed history reads from unsupported history', async () => {
    await evaluate(`window.changeOwner('automation-owner')`)
    await evaluate(`window.gotoWorkRecord('triggers', 'event:missing-native-trigger')`)
    await waitFor(`document.querySelector('[aria-label="Missing trigger"]')?.textContent.includes('event:missing-native-trigger')`)
    expect(await fetch(`${api}/api/triggers/event%3Amissing-native-trigger/history`, {
      headers: { Authorization: `Bearer ${credential}` },
    }).then(response => response.status)).toBe(404)

    await evaluate(`window.gotoWorkRecord('triggers', '${triggerId}')`)
    await waitFor(`document.querySelector('[aria-label="Trigger run history"]')?.textContent.includes('does not expose run history')`)
    await browser!.command('Network.setBlockedURLs', { urls: ['*api/triggers/*/history*'] })
    try {
      await click('Refresh')
      await waitFor(`document.querySelector('[aria-label="Trigger run history"] [role="alert"]')?.textContent.includes('could not be loaded')`)
      expect(await evaluate(`document.querySelector('[aria-label="Trigger run history"]')?.textContent`)).not.toContain('does not expose run history')
    } finally {
      await browser!.command('Network.setBlockedURLs', { urls: [] })
    }
    await click('Retry run history')
    await waitFor(`document.querySelector('[aria-label="Trigger run history"]')?.textContent.includes('does not expose run history')`)
  }, 30000)

  it('retains native loop plan drafts after a committed comment loses its acknowledgement and routes loop creation', async () => {
    await evaluate(`window.gotoWork('loops')`)
    await waitFor(`document.querySelector('[aria-label="Loop catalogue"]')?.textContent.includes('${loopId}')`)
    await click(loopId)
    await waitFor(`document.querySelector('[aria-label="Loop plan and review"]')?.textContent.includes('Review current state')`)
    await waitFor(`document.querySelector('[aria-label="Loop status"]')?.textContent.includes('Live updates connected.')`)
    await waitFor(`document.querySelector('[aria-label="Loop status"]')?.textContent.includes('Which follow-up should happen next?')`)
    expect(await evaluate(`window.currentRoute?.placement?.id`)).toBe('loops/run')
    expect(await evaluate(`document.querySelector('[aria-label="Loop report"]')?.textContent`)).toContain('Native fixture report')
    const connectedStream = await fetch(`${api}/__test/loop-stream-count`).then(response => response.json()) as { active: number }
    expect(connectedStream.active).toBeGreaterThan(0)
    const disconnect = await fetch(`${api}/__test/disconnect-loop-stream`, { method: 'POST' }).then(response => response.json()) as { disconnected: number }
    expect(disconnect.disconnected).toBeGreaterThan(0)
    await waitFor(`document.querySelector('[aria-label="Loop status"]')?.textContent.includes('Live updates disconnected')`)
    await click('Reconnect live updates')
    await waitFor(`document.querySelector('[aria-label="Loop status"]')?.textContent.includes('Live updates connected.')`)
    const recoveredStream = await fetch(`${api}/__test/loop-stream-count`).then(response => response.json()) as { active: number }
    expect(recoveredStream.active).toBeGreaterThan(0)
    const note = 'Keep the source run linked in the final report.'
    await evaluate(`(() => { const node = document.querySelector('[aria-label="Loop plan and review"] textarea'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(node, ${JSON.stringify(note)}); node.dispatchEvent(new Event('input', {bubbles:true})) })()`)
    await fetch(`${api}/__test/hold-next-native-write`, { method: 'POST' })
    await click('Send feedback')
    const waiting = await fetch(`${api}/__test/native-write-started`).then(response => response.json()) as { started: boolean }
    expect(waiting.started).toBe(true)
    await fetch(`${api}/__test/release-native-write`, { method: 'POST' })
    await waitFor(`document.querySelector('[role="alert"]')?.textContent.includes('acknowledgement was disconnected')`)
    expect(await evaluate(`document.querySelector('[aria-label="Loop plan and review"] textarea')?.value`)).toBe(note)
    const nativePlan = await fetch(`${api}/api/loops/${loopId}/plan-session`, { headers: { Authorization: `Bearer ${credential}` } }).then(response => response.json()) as { session: { steps: Array<{ comments: Array<{ text: string }> }> } }
    expect(nativePlan.session.steps[0].comments.map(comment => comment.text)).toContain(note)
    await evaluate(`window.gotoWork('loops/new')`)
    await waitFor(`document.querySelector('[aria-label="New loop intake"]')`)
    const firstOwnerTask = 'Map a native loop workflow and preserve its report links.'
    await evaluate(`(() => { const node = document.querySelector('[aria-label="New loop intake"] textarea'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(node, ${JSON.stringify(firstOwnerTask)}); node.dispatchEvent(new Event('input', {bubbles:true})) })()`)
    await fetch(`${api}/__test/hold-loop-preflight`, { method: 'POST' })
    await click('Check readiness and create')
    const preflightStarted = await fetch(`${api}/__test/loop-preflight-started`).then(response => response.json()) as { started: boolean }
    expect(preflightStarted.started).toBe(true)
    await evaluate(`window.changeOwner('automation-owner-final')`)
    await waitFor(`document.querySelector('[aria-label="New loop intake"]')`)
    expect(await evaluate(`document.querySelector('[aria-label="New loop intake"] textarea')?.value`)).toBe('')
    const nextOwnerTask = 'Create the second owners native loop and keep the report source.'
    await evaluate(`(() => { const node = document.querySelector('[aria-label="New loop intake"] select'); Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(node, 'research'); node.dispatchEvent(new Event('change', {bubbles:true})) })()`)
    await evaluate(`(() => { const node = document.querySelector('[aria-label="New loop intake"] textarea'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(node, ${JSON.stringify(nextOwnerTask)}); node.dispatchEvent(new Event('input', {bubbles:true})) })()`)
    await fetch(`${api}/__test/release-loop-preflight`, { method: 'POST' })
    await new Promise(resolve => setTimeout(resolve, 150))
    expect(await evaluate(`window.currentRoute?.placement?.id`)).toBe('loops/new')
    expect(await evaluate(`document.querySelector('[aria-label="New loop intake"] textarea')?.value`)).toBe(nextOwnerTask)
    const noStaleCreate = await fetch(`${api}/__test/stats`).then(response => response.json()) as { loop_create_attempts: number }
    expect(noStaleCreate.loop_create_attempts).toBe(0)
    await click('Check readiness and create')
    await waitFor(`window.currentRoute?.placement?.id === 'loops/run'`)
    await waitFor(`document.querySelector('[aria-label="Loop status"]')?.textContent.includes('Status: ready')`)
    expect(await evaluate(`window.currentRoute?.record?.kind`)).toBe('loop')
    const createdStats = await fetch(`${api}/__test/stats`).then(response => response.json()) as { loop_create_attempts: number }
    expect(createdStats.loop_create_attempts).toBe(1)
    await evaluate('location.reload()')
    await waitFor(`document.querySelector('[aria-label="Loop status"]')?.textContent.includes('Status: ready')`)
  }, 30000)
})
