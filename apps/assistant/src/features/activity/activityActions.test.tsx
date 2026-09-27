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
  for (const child of children) child.kill('SIGTERM')
  await Promise.all(children.map(child => child.exitCode === null
    ? new Promise<void>(resolveExit => child.once('close', () => resolveExit())) : Promise.resolve()))
  socket?.close()
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
  const directory = await mkdtemp(join(tmpdir(), 'gideon-activity-actions-'))
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
  throw new Error(`Activity action condition did not appear: ${expression}`)
}

describe('Gideon Activity native review and continuation', () => {
  it('decides one exact approval revision, refreshes on stale target, and continues its linked Chat', async () => {
    const webPort = await port()
    const origin = `http://127.0.0.1:${webPort}`
    const viteCache = await mkdtemp(join(tmpdir(), 'gideon-activity-actions-vite-'))
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
      configFile: false, root: join(root, 'apps/assistant'), cacheDir: viteCache,
      resolve: { alias: { 'react-native': 'react-native-web' } },
      optimizeDeps: { include: ['react', 'react-dom', 'react-dom/client', 'react-native-web'] },
      plugins: [{
        name: 'activity-actions-integration',
        resolveId(id) { if (id === '/activity-actions-entry.tsx') return '\0activity-actions-entry' },
        load(id) { if (id === '\0activity-actions-entry') return `
import React from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import ActivityDetail from '/src/features/activity/ActivityDetail.web.tsx'
import { signInOwner, ownerScope } from '/src/shared/auth.web.tsx'
import { createShellRoute, serializeShellRoute, parseShellRoute } from '/src/shared/shell/shellRoutes.ts'
import { ShellThemeProvider } from '/src/shared/shell/shellTheme.web.ts'
const root = createRoot(document.getElementById('root'))
window.mountRecord = record => {
  window.route = createShellRoute('activity', { view: 'detail', record,
    returnTo: { destination: 'activity', placement: { id: 'activity', query: { view: 'attention' } } } })
  flushSync(() => root.render(React.createElement(ShellThemeProvider, null,
    React.createElement(ActivityDetail, { route: window.route, scope: window.scope,
      navigate: window.navigateActivity, onReturn: () => {} }))))
}
window.navigateActivity = next => {
  const url = serializeShellRoute(next)
  const parsed = parseShellRoute(url, location.origin)
  if (parsed.kind !== 'route') throw new Error('Continuation route did not parse')
  window.route = parsed
  history.pushState(null, '', url)
  if (parsed.destination === 'chat') {
    flushSync(() => root.render(React.createElement('main', { 'data-chat-session': parsed.sessionId }, 'Chat session')))
  }
}
window.beginActions = async () => {
  const owner = await signInOwner('owner-a', 'correct-horse-battery-staple')
  window.scope = ownerScope(location.origin, owner)
  window.mountRecord({ kind: 'approval', id: 'approval-1' })
}
window.loaded = true
` },
        configureServer(server) { server.middlewares.use('/integration', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><html><head><style>html,body,#root{margin:0;width:100%;height:100%;display:flex;overflow:hidden}</style></head><body><div id="root"></div><script type="module" src="/activity-actions-entry.tsx"></script></body></html>')
        }) },
      }],
      server: { host: '127.0.0.1', port: webPort, strictPort: true,
        proxy: { '/api': `http://127.0.0.1:${api.api_port}` } },
    })
    await vite.listen()
    const { evaluate } = await chromium(`${origin}/integration`)
    await until(evaluate, 'window.loaded === true')
    await evaluate('window.beginActions()')
    await until(evaluate, `document.querySelector('[data-approval-id="approval-1"]')?.getAttribute('data-approval-revision')`)
    const oldRevision = await evaluate(`document.querySelector('[data-approval-id="approval-1"]').getAttribute('data-approval-revision')`)
    expect(oldRevision).toMatch(/^[a-f0-9]{32}$/)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('send_message')`)).toBe(true)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('first exact request')`)).toBe(true)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('Allowed decisions: approve this request or reject this request.')`)).toBe(true)

    await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Continue in Chat').click()`)
    expect(await evaluate(`window.route.destination`)).toBe('chat')
    expect(await evaluate(`window.route.sessionId`)).toBe('chat-1')
    expect(await evaluate(`window.route.returnTo.destination`)).toBe('activity')
    await evaluate(`window.mountRecord({ kind: 'approval', id: 'approval-1' })`)
    await until(evaluate, `document.querySelector('[data-approval-id="approval-1"]')?.getAttribute('data-approval-revision')`)

    const control = `http://127.0.0.1:${api.control_port}`
    const replacement = await (await fetch(`${control}/replace-approval`, { method: 'POST' })).json() as { revision: string }
    expect(replacement.revision).not.toBe(oldRevision)
    await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Approve request').click()`)
    await until(evaluate, `document.querySelector('[role="alert"]')?.textContent.includes('changed or is no longer pending')`)
    await until(evaluate, `document.querySelector('[data-approval-id="approval-1"]')?.getAttribute('data-approval-revision') === ${JSON.stringify(replacement.revision)}`)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('delete_file')`)).toBe(true)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('/workspace/second.txt')`)).toBe(true)
    let state = await (await fetch(`${control}/approval-state`)).json() as { pending: string[]; results: Record<string, boolean[]> }
    expect(state.pending).toContain('approval-1')
    expect(state.results['approval-1']).toEqual([false])

    await evaluate(`window.mountRecord({ kind: 'inbox_item', id: 'inbox-1' })`)
    await until(evaluate, `document.querySelector('[data-activity-detail="inbox_item"]')?.getAttribute('data-read-state') === 'ready'`)
    expect(await evaluate(`Array.from(document.querySelectorAll('[role="alert"]')).some(item => item.textContent.includes('changed or is no longer pending'))`)).toBe(false)

    await evaluate(`window.mountRecord({ kind: 'approval', id: 'approval-1' })`)
    await until(evaluate, `document.querySelector('[data-approval-id="approval-1"]')?.getAttribute('data-approval-revision') === ${JSON.stringify(replacement.revision)}`)
    await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Reject request').click()`)
    await until(evaluate, `document.querySelector('[data-activity-detail]')?.getAttribute('data-read-state') === 'missing'`)
    state = await (await fetch(`${control}/approval-state`)).json() as typeof state
    expect(state.pending).not.toContain('approval-1')
    expect(state.results['approval-1']).toEqual([false, false])

    const next = await (await fetch(`${control}/replace-approval`, { method: 'POST' })).json() as { revision: string }
    await evaluate(`window.mountRecord({ kind: 'approval', id: 'approval-1' })`)
    await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Reload record').click()`)
    await until(evaluate, `document.querySelector('[data-approval-id="approval-1"]')?.getAttribute('data-approval-revision') === ${JSON.stringify(next.revision)}`)
    await fetch(`${control}/replace-on-success`, { method: 'POST' })
    await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Approve request').click()`)
    await until(evaluate, `document.querySelector('[data-approval-id="approval-1"]')?.getAttribute('data-approval-revision') !== ${JSON.stringify(next.revision)}`)
    const immediatelyReplacedRevision = await evaluate(`document.querySelector('[data-approval-id="approval-1"]').getAttribute('data-approval-revision')`)
    expect(immediatelyReplacedRevision).toMatch(/^[a-f0-9]{32}$/)
    expect(immediatelyReplacedRevision).not.toBe(next.revision)
    expect(await evaluate(`document.querySelector('[data-activity-detail]')?.textContent.includes('/workspace/third.txt')`)).toBe(true)
    expect(await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Approve request').disabled`)).toBe(false)
    state = await (await fetch(`${control}/approval-state`)).json() as typeof state
    expect(state.pending).toContain('approval-1')
    expect(state.results['approval-1']).toEqual([false, false, true])

    await evaluate(`window.mountRecord({ kind: 'inbox_item', id: 'inbox-1' })`)
    await until(evaluate, `document.querySelector('[data-activity-detail="inbox_item"]')?.getAttribute('data-read-state') === 'ready'`)
    await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Continue in Chat').click()`)
    expect(await evaluate(`window.route.destination`)).toBe('chat')
    expect(await evaluate(`window.route.sessionId`)).toBe('chat-1')
    expect(await evaluate(`window.route.returnTo.record`)).toEqual({ kind: 'inbox_item', id: 'inbox-1' })
  }, 30_000)
})
