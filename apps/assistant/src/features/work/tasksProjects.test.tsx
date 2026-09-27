import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer, type ViteDevServer } from 'vite'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { createShellRoute, parseShellRoute, serializeShellRoute } from '../../shared/shell/shellRoutes'

let native: ChildProcessWithoutNullStreams | undefined
let browserProcess: ChildProcessWithoutNullStreams | undefined
let browserDirectory = ''
let socket: WebSocket | undefined
let vite: ViteDevServer | undefined
let api = ''
let controlApi = ''
let credential = ''
let taskId = ''
let projectId = ''
let evaluate: (expression: string) => Promise<any>

const nativeFetch = (path: string, init: RequestInit = {}) => fetch(`${api}${path}`, {
  ...init, headers: { Authorization: `Bearer ${credential}`, ...init.headers },
})
const nativePut = (path: string, body: Record<string, unknown>) => nativeFetch(path, {
  method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})
const control = async (action: string, path = '', method = 'GET') => {
  const query = path ? `?path=${encodeURIComponent(path)}&method=${method}` : ''
  const response = await fetch(`${controlApi}/test/${action}${query}`)
  if (!response.ok) throw new Error(`Fixture control ${action} failed: ${response.status}`)
  return response.json() as Promise<{ writes: { task: number; project: number }; held: boolean }>
}

async function port(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  const address = server.address()
  const result = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(done => server.close(() => done()))
  return result
}

async function waitFor(expression: string) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await evaluate(expression)) return
    await new Promise(done => setTimeout(done, 75))
  }
  throw new Error(`Browser condition did not become true: ${expression}`)
}

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  native = spawn(process.env.GIDEON_TEST_PYTHON || 'python3',
    [join(root, 'apps/assistant/test-support/work_server.py')], {
      env: { ...process.env, PYTHONPATH: join(root, 'runtime') },
    })
  const line = await new Promise<string>((done, reject) => {
    let output = ''
    let errors = ''
    const timer = setTimeout(() => reject(new Error(`Native server timed out: ${errors}`)), 15000)
    native!.stdout.on('data', chunk => {
      output += String(chunk)
      if (output.includes('\n')) { clearTimeout(timer); done(output.split('\n')[0]) }
    })
    native!.stderr.on('data', chunk => { errors += String(chunk) })
    native!.once('exit', code => { clearTimeout(timer); reject(new Error(`Native server exited ${code}: ${errors}`)) })
  })
  const ready = JSON.parse(line) as { port: number; control_port: number; credential: string;
    task_id: string; project_id: string }
  api = `http://127.0.0.1:${ready.port}`
  controlApi = `http://127.0.0.1:${ready.control_port}`
  credential = ready.credential
  taskId = ready.task_id
  projectId = ready.project_id
  const webPort = await port()
  vite = await createServer({
    configFile: false, root: join(root, 'apps/assistant'),
    resolve: { alias: [{ find: /^react-native$/, replacement: 'react-native-web' }] },
    optimizeDeps: { include: ['react', 'react-dom/client', 'react-native-web'] },
    plugins: [{
      name: 'task-project-workspaces',
      resolveId(id) { if (id === '/workspace-entry.ts') return '\0workspace-entry' },
      load(id) { if (id === '\0workspace-entry') return `
import React from 'react'
import { createRoot } from 'react-dom/client'
import TaskWorkspace from '/src/features/work/TaskWorkspace.web.tsx'
import ProjectWorkspace from '/src/features/work/ProjectWorkspace.web.tsx'
import { createShellRoute } from '/src/shared/shell/shellRoutes.ts'
const host = document.getElementById('root')
const root = createRoot(host)
const scope = { runtimeOrigin: location.origin, ownerId: 'native-owner', cacheKey: 'native-owner' }
window.renderWorkspace = (kind, id, owner = scope) => {
  const route = kind === 'task'
    ? createShellRoute('activity', { view: 'detail', placement: { id: 'tasks' }, record: { kind, id } })
    : createShellRoute('apps', { view: 'detail', placement: { id: 'projects/detail', subview: '/planning' }, record: { kind, id } })
  root.render(React.createElement(kind === 'task' ? TaskWorkspace : ProjectWorkspace,
    { route, scope: owner, navigate: next => { window.lastRoute = next }, onReturn: () => { window.returned = true } }))
}
window.setField = (label, value) => {
  const field = Array.from(document.querySelectorAll('label')).find(node => node.textContent.trim().startsWith(label))?.querySelector('input, textarea, select')
  if (!field) throw new Error('Missing field: ' + label)
  const setter = Object.getOwnPropertyDescriptor(field.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype
    : field.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value').set
  setter.call(field, value)
  field.dispatchEvent(new Event(field.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }))
}
window.clickNamed = name => {
  const button = Array.from(document.querySelectorAll('button')).find(node => node.textContent.trim() === name)
  if (!button) throw new Error('Missing button: ' + name)
  button.click()
}
window.loaded = true
` },
      configureServer(server) {
        server.middlewares.use('/integration', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><html><body><div id="root"></div><script type="module" src="/workspace-entry.ts"></script></body></html>')
        })
      },
    }],
    server: { host: '127.0.0.1', port: webPort, strictPort: true, proxy: {
      '/api': { target: api, changeOrigin: true, headers: { Authorization: `Bearer ${credential}` } },
    } },
  })
  await vite.listen()
  browserDirectory = await mkdtemp(join(tmpdir(), 'gideon-task-project-'))
  const debugPort = await port()
  browserProcess = spawn(process.env.CHROMIUM_BIN || 'chromium', [
    '--headless', '--no-sandbox', '--disable-gpu', '--disable-background-networking',
    `--remote-debugging-port=${debugPort}`, `--user-data-dir=${browserDirectory}`, 'about:blank',
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
    socket!.addEventListener('error', () => reject(new Error('Debugger failed')), { once: true })
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
    const id = ++next
    pending.set(id, { done, reject })
    socket!.send(JSON.stringify({ id, method, params }))
  })
  await command('Page.navigate', { url: `http://127.0.0.1:${webPort}/integration` })
  evaluate = async expression => {
    const response = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }) as
      { result?: { value?: any }; exceptionDetails?: { text?: string; exception?: { description?: string } } }
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text)
    return response.result?.value
  }
  await waitFor('window.loaded === true')
  await new Promise(done => setTimeout(done, 1000))
  await waitFor('typeof window.renderWorkspace === \"function\"')
}, 30000)

afterAll(async () => {
  socket?.close()
  browserProcess?.kill('SIGTERM')
  if (vite) await vite.close()
  if (browserDirectory) await rm(browserDirectory, { recursive: true, force: true })
  native?.kill('SIGTERM')
})

describe('native task and project workspaces', () => {
  it('preserves one project identity across Work planning and Code handoff', () => {
    const origin = { destination: 'activity' as const, placement: { id: 'tasks' }, sessionId: 'origin-session' }
    const route = createShellRoute('apps', { view: 'detail', placement: { id: 'projects/detail', subview: '/planning' },
      record: { kind: 'project', id: projectId }, returnTo: origin })
    expect(parseShellRoute(serializeShellRoute(route))).toEqual(route)
    expect(route.record?.id).toBe(projectId)
    expect(route.returnTo).toEqual(origin)
  })

  it('reviews a real task, posts a native comment, and saves a native edit', async () => {
    await waitFor('typeof window.renderWorkspace === \"function\"')
    await evaluate(`window.renderWorkspace('task', ${JSON.stringify(taskId)})`)
    await waitFor('document.querySelector("[aria-label=\\"Task plan\\"]")?.textContent.includes("Save task")')
    expect(await evaluate('document.querySelector("main")?.getAttribute("data-workspace-mode")')).toBe('full')
    expect(await evaluate('document.body.textContent.includes("Source task")')).toBe(true)
    await evaluate("window.setField('Add a comment', 'Reviewed on mobile')")
    await evaluate("window.clickNamed('Post comment')")
    await waitFor('document.body.textContent.includes("Comment added.")')
    const commentResponse = await nativeFetch(`/api/tasks/${encodeURIComponent(taskId)}/comments`)
    const comments = await commentResponse.json() as { comments: Array<{ body: string }> }
    expect(comments.comments.some(row => row.body === 'Reviewed on mobile')).toBe(true)
    await evaluate("window.setField('Description', 'Saved through task workspace')")
    await evaluate("window.clickNamed('Save task')")
    await waitFor('document.body.textContent.includes("Task saved.")')
    const saved = await (await nativeFetch(`/api/tasks/${encodeURIComponent(taskId)}`)).json() as { description: string }
    expect(saved.description).toBe('Saved through task workspace')
  }, 30000)

  it('keeps a task draft and native ID after an external revision change', async () => {
    await waitFor('typeof window.renderWorkspace === \"function\"')
    await evaluate(`window.renderWorkspace('task', ${JSON.stringify(taskId)})`)
    await waitFor('document.querySelector("[aria-label=\\"Task plan\\"]")?.textContent.includes("Save task")')
    await evaluate("window.setField('Description', 'Draft survives conflict')")
    await new Promise(done => setTimeout(done, 1100))
    const changed = await nativeFetch(`/api/tasks/${encodeURIComponent(taskId)}`, { method: 'PUT',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ description: 'External revision' }) })
    expect(changed.ok).toBe(true)
    await evaluate("window.clickNamed('Save task')")
    await waitFor('document.querySelector("[role=alert]")?.textContent.includes("draft is preserved")')
    expect(await evaluate('document.querySelector("label:has(textarea)")?.querySelector("textarea")?.value')).toBe('Draft survives conflict')
    expect(await evaluate('document.body.textContent.includes(' + JSON.stringify(taskId) + ')')).toBe(true)
  }, 30000)

  it('opens the same native project in Code and preserves a failed project draft', async () => {
    await waitFor('typeof window.renderWorkspace === \"function\"')
    await evaluate(`window.renderWorkspace('project', ${JSON.stringify(projectId)})`)
    await waitFor('document.querySelector("[aria-label=\\"Project context\\"]")?.textContent.includes("Save project")')
    await evaluate("window.clickNamed('Open code workspace')")
    expect(await evaluate('window.lastRoute?.record?.id')).toBe(projectId)
    expect(await evaluate('window.lastRoute?.placement?.id')).toBe('projects/detail')
    const existing = await nativeFetch('/api/projects', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'Existing project for rejection' }) })
    expect(existing.status).toBe(201)
    await evaluate("window.setField('Project name', 'Existing project for rejection')")
    await evaluate("window.clickNamed('Save project')")
    await waitFor('document.querySelector("[role=alert]")?.textContent.includes("already exists")')
    expect(await evaluate('Array.from(document.querySelectorAll("label")).find(node => node.textContent.startsWith("Project name"))?.querySelector("input")?.value')).toBe('Existing project for rejection')
    expect(await evaluate('document.body.textContent.includes(' + JSON.stringify(projectId) + ')')).toBe(true)
  }, 30000)

  it('creates a native project task and keeps project membership visible', async () => {
    await waitFor('typeof window.renderWorkspace === \"function\"')
    await evaluate(`window.renderWorkspace('project', ${JSON.stringify(projectId)})`)
    await waitFor('document.querySelector("[aria-label=\\"Project task planning\\"]")?.textContent.includes("Create task")')
    await evaluate("window.setField('New task title', 'Planned native task')")
    await evaluate("window.clickNamed('Create task')")
    try {
      await waitFor('document.body.textContent.includes("Planned native task")')
    } catch (error) {
      const body = await evaluate('document.body.textContent')
      const nativeResponse = await (await nativeFetch('/api/tasks')).text()
      throw new Error(`${String(error)}\nBrowser: ${body}\nNative: ${nativeResponse}`)
    }
    const membership = await (await nativeFetch(`/api/task-lists?project_id=${encodeURIComponent(projectId)}`)).json() as
      { task_lists: Array<{ id: string }> }
    const pages = await Promise.all(membership.task_lists.map(list =>
      nativeFetch(`/api/tasks?task_list_id=${encodeURIComponent(list.id)}`).then(response => response.json() as Promise<{
        tasks: Array<{ id: string; title: string; task_list_id: string }>
      }>)))
    expect(pages.flatMap(page => page.tasks).some(row => row.title === 'Planned native task' &&
      !!row.id && membership.task_lists.some(list => list.id === row.task_list_id))).toBe(true)
  }, 30000)

  it('does not send an old task edit after the owner changes during native preflight', async () => {
    await evaluate(`window.renderWorkspace('task', ${JSON.stringify(taskId)})`)
    await waitFor('document.querySelector("[aria-label=\\"Task plan\\"]")?.textContent.includes("Save task")')
    await evaluate("window.setField('Description', 'Must not cross owner scope')")
    const before = await control('state')
    await control('arm', `/api/tasks/${encodeURIComponent(taskId)}`)
    await evaluate("window.clickNamed('Save task')")
    await control('wait')
    await evaluate(`window.renderWorkspace('task', ${JSON.stringify(taskId)}, { runtimeOrigin: location.origin, ownerId: 'other-owner', cacheKey: 'other-owner' })`)
    await waitFor('document.body.textContent.includes("Owner: other-owner")')
    await control('release')
    await control('settled')
    await new Promise(done => setTimeout(done, 150))
    const after = await control('state')
    expect(after.writes.task).toBe(before.writes.task)
    const record = await (await nativeFetch(`/api/tasks/${encodeURIComponent(taskId)}`)).json() as { description: string }
    expect(record.description).not.toBe('Must not cross owner scope')
  }, 30000)

  it('does not send an old project edit after the route changes during native preflight', async () => {
    await evaluate(`window.renderWorkspace('project', ${JSON.stringify(projectId)})`)
    await waitFor('document.querySelector("[aria-label=\\"Project context\\"]")?.textContent.includes("Save project")')
    await evaluate("window.setField('Brief', 'Must not cross project route')")
    const before = await control('state')
    await control('arm', `/api/projects/${encodeURIComponent(projectId)}`)
    await evaluate("window.clickNamed('Save project')")
    await control('wait')
    await evaluate(`window.renderWorkspace('task', ${JSON.stringify(taskId)})`)
    await waitFor('document.querySelector("[aria-label=\\"Task plan\\"]")?.textContent.includes("Save task")')
    await control('release')
    await control('settled')
    await new Promise(done => setTimeout(done, 150))
    const after = await control('state')
    expect(after.writes.project).toBe(before.writes.project)
    const record = await (await nativeFetch(`/api/projects/${encodeURIComponent(projectId)}`)).json() as { brief: string }
    expect(record.brief).not.toBe('Must not cross project route')
  }, 30000)

  it('accepts exactly one native task write for a shared revision and keeps legacy writes working', async () => {
    const path = `/api/tasks/${encodeURIComponent(taskId)}`
    const initial = await (await nativeFetch(path)).json() as { updated_at: string }
    const results = await Promise.all([
      nativePut(path, { description: 'Competing task write A', expected_revision: initial.updated_at }),
      nativePut(path, { description: 'Competing task write B', expected_revision: initial.updated_at }),
    ])
    expect(results.map(row => row.status).sort()).toEqual([200, 409])
    const conflict = await results.find(row => row.status === 409)!.json() as { error: { code: string } }
    expect(conflict.error.code).toBe('version_conflict')
    const winner = await (await nativeFetch(path)).json() as { description: string; updated_at: string }
    expect(['Competing task write A', 'Competing task write B']).toContain(winner.description)
    expect(winner.updated_at).not.toBe(initial.updated_at)
    const legacy = await nativePut(path, { description: 'Legacy task update' })
    expect(legacy.status).toBe(200)
    const final = await (await nativeFetch(path)).json() as { description: string; updated_at: string }
    expect(final.description).toBe('Legacy task update')
    expect(final.updated_at).not.toBe(winner.updated_at)
  }, 30000)

  it('accepts exactly one native project write for a shared revision and keeps legacy writes working', async () => {
    const path = `/api/projects/${encodeURIComponent(projectId)}`
    const initial = await (await nativeFetch(path)).json() as { updated_at: string }
    const results = await Promise.all([
      nativePut(path, { brief: 'Competing project write A', expected_revision: initial.updated_at }),
      nativePut(path, { brief: 'Competing project write B', expected_revision: initial.updated_at }),
    ])
    expect(results.map(row => row.status).sort()).toEqual([200, 409])
    const conflict = await results.find(row => row.status === 409)!.json() as { error: { code: string } }
    expect(conflict.error.code).toBe('version_conflict')
    const winner = await (await nativeFetch(path)).json() as { brief: string; updated_at: string }
    expect(['Competing project write A', 'Competing project write B']).toContain(winner.brief)
    expect(winner.updated_at).not.toBe(initial.updated_at)
    const legacy = await nativePut(path, { brief: 'Legacy project update' })
    expect(legacy.status).toBe(200)
    const final = await (await nativeFetch(path)).json() as { brief: string; updated_at: string }
    expect(final.brief).toBe('Legacy project update')
    expect(final.updated_at).not.toBe(winner.updated_at)
  }, 30000)

  it('preserves a task draft and ID when the native record changes after preflight', async () => {
    await evaluate(`window.renderWorkspace('project', ${JSON.stringify(projectId)})`)
    await waitFor('document.querySelector("[aria-label=\\"Project context\\"]")?.textContent.includes("Save project")')
    await evaluate(`window.renderWorkspace('task', ${JSON.stringify(taskId)})`)
    await waitFor('document.querySelector("[aria-label=\\"Task plan\\"]")?.textContent.includes("Save task")')
    await evaluate("window.setField('Description', 'Draft after task write race')")
    const path = `/api/tasks/${encodeURIComponent(taskId)}`
    await control('arm', path, 'PUT')
    await evaluate("window.clickNamed('Save task')")
    await control('wait')
    expect((await nativePut(path, { description: 'Concurrent task edit' })).status).toBe(200)
    await control('release')
    await control('settled')
    await waitFor('document.querySelector("[role=alert]")?.textContent.includes("changed while saving")')
    expect(await evaluate('Array.from(document.querySelectorAll("label")).find(node => node.textContent.startsWith("Description"))?.querySelector("textarea")?.value'))
      .toBe('Draft after task write race')
    expect(await evaluate('document.body.textContent.includes(' + JSON.stringify(taskId) + ')')).toBe(true)
    const final = await (await nativeFetch(path)).json() as { description: string }
    expect(final.description).toBe('Concurrent task edit')
  }, 30000)

  it('preserves a project draft and ID when the native record changes after preflight', async () => {
    await evaluate(`window.renderWorkspace('project', ${JSON.stringify(projectId)})`)
    await waitFor('document.querySelector("[aria-label=\\"Project context\\"]")?.textContent.includes("Save project")')
    await evaluate("window.setField('Brief', 'Draft after project write race')")
    const path = `/api/projects/${encodeURIComponent(projectId)}`
    await control('arm', path, 'PUT')
    await evaluate("window.clickNamed('Save project')")
    await control('wait')
    expect((await nativePut(path, { brief: 'Concurrent project edit' })).status).toBe(200)
    await control('release')
    await control('settled')
    await waitFor('document.querySelector("[role=alert]")?.textContent.includes("changed while saving")')
    expect(await evaluate('Array.from(document.querySelectorAll("label")).find(node => node.textContent.startsWith("Brief"))?.querySelector("textarea")?.value'))
      .toBe('Draft after project write race')
    expect(await evaluate('document.body.textContent.includes(' + JSON.stringify(projectId) + ')')).toBe(true)
    const final = await (await nativeFetch(path)).json() as { brief: string }
    expect(final.brief).toBe('Concurrent project edit')
  }, 30000)

  it('retains a task draft and source ID after its real credential is revoked', async () => {
    await evaluate(`window.renderWorkspace('task', ${JSON.stringify(taskId)})`)
    await waitFor('document.querySelector("[aria-label=\\"Task plan\\"]")?.textContent.includes("Save task")')
    await evaluate("window.setField('Description', 'Draft after credential revocation')")
    await control('revoke')
    await evaluate("window.clickNamed('Save task')")
    await waitFor('document.querySelector("[role=alert]")?.textContent.includes("Access denied")')
    expect(await evaluate('Array.from(document.querySelectorAll("label")).find(node => node.textContent.startsWith("Description"))?.querySelector("textarea")?.value'))
      .toBe('Draft after credential revocation')
    expect(await evaluate('document.body.textContent.includes(' + JSON.stringify(taskId) + ')')).toBe(true)
  }, 30000)
})
