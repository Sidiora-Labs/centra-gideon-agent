import * as React from 'react'
import { spawn, execFileSync, type ChildProcess } from 'node:child_process'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { gatewayJson, GatewayError } from '../../shared/transport.web'
import { parseShellRoute, serializeShellRoute, type ShellReturnContext } from '../../shared/shell/shellRoutes'
import { captureSavedContext, codeIndexRoute, codeRecord, codeReturnRoute, codeRoute, deleteSavedContext,
  readCodeSelection, readLiveTerminals, readProjectTasks, readSavedContexts, readWorkspaceProjects, reconcileSavedContext, resolveCodeRoute,
  type LiveComparison, type SavedContext } from './codeRoute'
import { SavedContextDetails } from './CodeWorkspace.web'

const conversation: ShellReturnContext = {
  destination: 'chat', sessionId: 'conversation-17', selectionId: 'message-4', scrollY: 512,
  placement: { id: 'chat/session', subview: '/messages', query: { selected: 'message-4' } },
}

const nativeFileServer = String.raw`
import asyncio, json, os
from aiohttp import web
from gideon.interfaces.dashboard.handlers import api_file_list, api_file_read, api_file_write

async def main():
    app = web.Application()
    app.router.add_get('/api/file-list', api_file_list)
    app.router.add_get('/api/file-read', api_file_read)
    app.router.add_post('/api/file-write', api_file_write)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    print(json.dumps({'origin': f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()

asyncio.run(main())
`

describe('Code workspace routes', () => {
  it('round trips distinct native project, saved context and Code run IDs with conversation return', () => {
    for (const record of [
      { kind: 'project' as const, id: 'project/7' },
      { kind: 'snapshot' as const, id: 'snapshot-8' },
      { kind: 'code-project' as const, id: 'loop-9' },
    ]) {
      const route = codeRoute(record, conversation)
      const parsed = parseShellRoute(serializeShellRoute(route))
      expect(parsed).toEqual(route)
      if (parsed.kind !== 'route') throw new Error('A Code route must be valid')
      expect(codeRecord(parsed)).toEqual(record)
      expect(codeReturnRoute(parsed)).toMatchObject({ destination: 'chat', sessionId: 'conversation-17',
        placement: conversation.placement })
    }
  })

  it('does not reinterpret a Code run as a workspace project or a saved context', () => {
    const project = codeRoute({ kind: 'project', id: 'same-id' })
    const run = codeRoute({ kind: 'code-project', id: 'same-id' })
    const context = codeRoute({ kind: 'snapshot', id: 'same-id' })
    expect(new Set([project.placement?.id, run.placement?.id, context.placement?.id]).size).toBe(3)
    expect(codeRecord({ ...run, record: { kind: 'project', id: 'same-id' } })).toBeNull()
    expect(codeRecord(codeIndexRoute())).toBeNull()
    expect(codeIndexRoute().placement?.id).toBe('projects')
  })

  it('shows missing references and changed live state without changing the saved record', () => {
    const context: SavedContext = { id: 'snapshot-8', project_id: 'project-7', workspace: '/work/project',
      branch: 'main', dirty: false, terminal_ids: ['terminal-1', 'terminal-2'], task_ids: ['task-1'],
      captured_at: '2026-09-27T10:00:00Z', revision: 3 }
    const comparison: LiveComparison = { snapshot: context, live: { branch: 'feature', dirty: true },
      branch_matches: false, surviving_terminal_ids: ['terminal-1'], missing_terminal_ids: ['terminal-2'],
      surviving_task_ids: [], missing_task_ids: ['task-1'] }
    const html = renderToStaticMarkup(<SavedContextDetails context={context} comparison={comparison} busy={false}
      onCompare={() => {}} onDelete={() => {}} />)
    expect(html).toContain('Saved branch: main')
    expect(html).toContain('Live branch: feature')
    expect(html).toContain('Missing terminals: terminal-2')
    expect(html).toContain('Missing tasks: task-1')
    expect(html).toContain('Compare with live workspace')
    expect(html).toContain('Delete saved context')
    expect(context.revision).toBe(3)
  })
})

describe('Code workspace against native owner records', () => {
  let backend: ChildProcess
  let origin = ''
  let token = ''
  let repo = ''
  const originalFetch = globalThis.fetch

  beforeAll(async () => {
    const root = resolve(process.cwd(), '../..')
    backend = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', [resolve(root, 'checks/runtime/capabilities/workspace/ui_server.py')],
      { env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
    const ready = await new Promise<{ url: string; repo: string; token: string }>((accept, reject) => {
      let output = '', errors = ''
      backend.stderr!.on('data', chunk => { errors = (errors + String(chunk)).slice(-4000) })
      backend.on('exit', code => reject(new Error(`Native workspace server exited ${code}: ${errors}`)))
      backend.stdout!.on('data', chunk => {
        output += String(chunk)
        const line = output.split('\n').find(text => text.startsWith('{"url"'))
        if (line) accept(JSON.parse(line))
      })
    })
    origin = ready.url; token = ready.token; repo = ready.repo
    globalThis.fetch = (input, init) => {
      const url = new URL(String(input), origin)
      url.searchParams.set('token', token)
      return originalFetch(url, init)
    }
  }, 30000)

  afterAll(() => { globalThis.fetch = originalFetch; backend?.kill() })

  it('registers, captures, reloads, reconciles and revision deletes real workspace records', async () => {
    const registered = await gatewayJson<{ project: { id: string } }>('/api/capabilities/workspace/projects', {
      method: 'POST', body: { name: 'Code workspace test', workspace: repo, request_id: crypto.randomUUID() },
    })
    const projects = await readWorkspaceProjects()
    const project = projects.find(item => item.project.id === registered.project.id)
    expect(project?.project.workspace_dir).toBe(repo)
    if (!project) throw new Error('Registered project was absent')
    const saved = await captureSavedContext(project, { terminal_ids: [], task_ids: [] }, crypto.randomUUID())
    expect(saved.project_id).toBe(project.project.id)
    expect((await readSavedContexts()).some(item => item.id === saved.id)).toBe(true)
    const selection = await readCodeSelection({ kind: 'snapshot', id: saved.id })
    expect(selection.context?.workspace).toBe(repo)
    expect(selection.project?.project.id).toBe(project.project.id)
    const scope = { runtimeOrigin: origin, ownerId: 'workspace-browser', cacheKey: JSON.stringify([origin, 'workspace-browser']) }
    expect(await resolveCodeRoute(scope, codeRoute({ kind: 'project', id: project.project.id }))).toBe('available')
    execFileSync('git', ['-C', repo, 'checkout', '-b', 'code-workspace-next'])
    const comparison = await reconcileSavedContext(saved)
    expect(comparison.branch_matches).toBe(false)
    expect(comparison.live.branch).toBe('code-workspace-next')
    await expect(gatewayJson(`/api/capabilities/workspace/${saved.id}?revision=0`, { method: 'DELETE' }))
      .rejects.toMatchObject({ status: 409 } satisfies Partial<GatewayError>)
    await deleteSavedContext(saved)
    expect((await readSavedContexts()).some(item => item.id === saved.id)).toBe(false)
    expect(await resolveCodeRoute(scope, codeRoute({ kind: 'snapshot', id: saved.id }))).toBe('missing')
  }, 30000)

  it('keeps owner-safe capture, opens and saves native project files, and returns from Work planning to Code', async () => {
    const first = await gatewayJson<{ project: { id: string } }>('/api/capabilities/workspace/projects', {
      method: 'POST', body: { name: 'First workspace', workspace: repo, request_id: crypto.randomUUID() },
    })
    const second = await gatewayJson<{ project: { id: string } }>('/api/capabilities/workspace/projects', {
      method: 'POST', body: { name: 'Second workspace', workspace: repo, request_id: crypto.randomUUID() },
    })
    const nativeProject = await gatewayJson<{ id: string }>('/api/projects', {
      method: 'POST', body: { name: 'Native Code and planning project', workspace_dir: repo },
    })
    const firstTask = await gatewayJson<{ id: string }>('/api/tasks', {
      method: 'POST', body: { title: 'Review first workspace', project_id: first.project.id },
    })
    const secondTask = await gatewayJson<{ id: string }>('/api/tasks', {
      method: 'POST', body: { title: 'Review second workspace', project_id: second.project.id },
    })
    const terminal = (await readLiveTerminals(repo))[0]
    expect(terminal?.session_id).toBe('native-workspace-terminal')
    expect((await readProjectTasks(first.project.id)).map(item => item.id)).toContain(firstTask.id)
    expect((await readProjectTasks(second.project.id)).map(item => item.id)).toContain(secondTask.id)
    const portServer = createNetServer()
    await new Promise<void>(done => portServer.listen(0, '127.0.0.1', done))
    const address = portServer.address()
    const port = typeof address === 'object' && address ? address.port : 0
    await new Promise<void>(done => portServer.close(() => done()))
    const browserPortServer = createNetServer()
    await new Promise<void>(done => browserPortServer.listen(0, '127.0.0.1', done))
    const browserAddress = browserPortServer.address()
    const browserPort = typeof browserAddress === 'object' && browserAddress ? browserAddress.port : 0
    await new Promise<void>(done => browserPortServer.close(() => done()))
    const directory = await mkdtemp(join(tmpdir(), 'gideon-code-browser-'))
    const files = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-c', nativeFileServer], {
      env: { ...process.env, GIDEON_HOME: directory, GIDEON_WORKSPACE: repo, PYTHONPATH: resolve(process.cwd(), '../..', 'runtime') },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    const fileOrigin = await new Promise<string>((accept, reject) => {
      let output = '', errors = ''
      files.stderr!.on('data', chunk => { errors = (errors + String(chunk)).slice(-4000) })
      files.on('exit', code => reject(new Error(`Native file handlers exited ${code}: ${errors}`)))
      files.stdout!.on('data', chunk => {
        output += String(chunk)
        const line = output.split('\n').find(value => value.startsWith('{"origin"'))
        if (line) accept(JSON.parse(line).origin)
      })
    })
    const initial = JSON.stringify({ first: first.project.id, second: second.project.id,
      nativeProject: nativeProject.id, firstTask: firstTask.id, secondTask: secondTask.id,
      terminal: terminal.session_id, token, conversation })
    const vite = await createServer({
      configFile: false, root: resolve(process.cwd()), cacheDir: join(directory, 'vite-cache'),
      resolve: { alias: [{ find: /^react-native$/, replacement: 'react-native-web' }],
        extensions: ['.web.tsx', '.web.ts', '.web.js', '.tsx', '.ts', '.js', '.jsx', '.json'],
        dedupe: ['react', 'react-dom'] },
      esbuild: { jsx: 'automatic' },
      optimizeDeps: { include: ['react', 'react-dom', 'react-dom/client', 'react-native-web', 'react/jsx-dev-runtime'],
        esbuildOptions: { resolveExtensions: ['.web.tsx', '.web.ts', '.web.js', '.tsx', '.ts', '.jsx', '.js', '.json'] } },
      plugins: [{ name: 'code-workspace-browser',
        resolveId(id) { if (id === '/entry-code-browser.tsx') return '\0code-browser' },
        load(id) { if (id !== '\0code-browser') return undefined; return `
          import React from 'react';
          import { createRoot } from 'react-dom/client';
          import { flushSync } from 'react-dom';
          import CodeWorkspace from '/src/features/code/CodeWorkspace.web.tsx';
          import { codeIndexRoute, codeRoute } from '/src/features/code/codeRoute.ts';
          import { ShellThemeProvider } from '/src/shared/shell/shellTheme.web.ts';
          const ids = ${initial};
          const nativeFetch = window.fetch.bind(window);
          window.__navigations = [];
          window.__actionReturned = false;
          window.__captureRequests = [];
          window.__delayCapture = true;
          window.fetch = async (input, init) => {
            const url = new URL(String(input), location.origin);
            if (url.pathname.startsWith('/api/')) url.searchParams.set('token', ids.token);
            if (url.pathname === '/api/capabilities/workspace' && init?.method === 'POST')
              window.__captureRequests.push(JSON.parse(init.body));
            const response = await nativeFetch(url, init);
            if (url.pathname === '/api/capabilities/workspace' && init?.method === 'POST' && window.__delayCapture) {
              window.__actionReturned = true;
              return new Promise(resolve => { window.__releaseResponse = () => resolve(response) });
            }
            return response;
          };
          function App() {
          const [view, setView] = React.useState({ owner: 'owner-a', project: ids.first, theme: 'dark', route: null });
          window.__switchCode = () => flushSync(() => setView({ owner: 'owner-b', project: ids.second, theme: 'dark' }));
          window.__openIndex = () => flushSync(() => setView(current => ({ ...current, project: null, route: null })));
          window.__openNativeProject = () => flushSync(() => setView(current => ({ ...current,
            project: ids.nativeProject, route: codeRoute({ kind: 'project', id: ids.nativeProject }, ids.conversation) })));
          window.__setCodeTheme = theme => flushSync(() => setView(current => ({ ...current, theme })));
          window.__ids = ids;
            const route = view.route ?? (view.project ? codeRoute({ kind: 'project', id: view.project }) : codeIndexRoute());
            return React.createElement(ShellThemeProvider, { key: view.theme, initialPreference: view.theme },
              React.createElement(CodeWorkspace, { route,
                scope: { ownerId: view.owner, runtimeOrigin: location.origin,
                  cacheKey: JSON.stringify([location.origin, view.owner]) },
                navigate: next => { window.__navigations.push(next); setView(current => ({ ...current, route: next })) },
                onReturn: () => {} }));
          }
          createRoot(document.getElementById('root')).render(React.createElement(App));
        ` },
        configureServer(server) { server.middlewares.use('/code-browser', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><html><body><div id="root"></div><script type="module" src="/entry-code-browser.tsx"></script></body></html>')
        }) },
      }],
      server: { host: '127.0.0.1', port, strictPort: true, proxy: { '/api/file-': fileOrigin, '/api': origin } },
    })
    let chrome: ChildProcess | undefined
    let socket: WebSocket | undefined
    try {
      await vite.listen()
      chrome = spawn(process.env.CHROMIUM_BIN || 'chromium', ['--headless', '--no-sandbox', '--disable-gpu',
        `--remote-debugging-port=${browserPort}`, `--user-data-dir=${directory}`, 'about:blank'], { stdio: 'ignore' })
      let target: { webSocketDebuggerUrl: string } | undefined
      for (let attempt = 0; attempt < 100 && !target; attempt++) {
        try { target = (await (await originalFetch(`http://127.0.0.1:${browserPort}/json`)).json())
          .find((item: { type: string }) => item.type === 'page') }
        catch { await new Promise(done => setTimeout(done, 100)) }
      }
      if (!target) throw new Error('Chromium debugger unavailable')
      socket = new WebSocket(target.webSocketDebuggerUrl)
      await new Promise<void>((done, fail) => {
        socket!.addEventListener('open', () => done(), { once: true })
        socket!.addEventListener('error', () => fail(new Error('Chromium connection failed')), { once: true })
      })
      const connection = socket
      let nextId = 0
      const browserErrors: string[] = []
      const pending = new Map<number, (value: any) => void>()
      connection.addEventListener('message', event => {
        const message = JSON.parse(String(event.data))
        if (message.method === 'Runtime.exceptionThrown' || message.method === 'Log.entryAdded')
          browserErrors.push(JSON.stringify(message.params))
        if (message.id && pending.has(message.id)) { pending.get(message.id)!(message); pending.delete(message.id) }
      })
      const send = (method: string, params: Record<string, unknown> = {}) => new Promise<any>(done => {
        const id = ++nextId; pending.set(id, done); connection.send(JSON.stringify({ id, method, params }))
      })
      const evaluate = async (expression: string) => {
        const answer = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
        if (answer.error || answer.result?.exceptionDetails) throw new Error(JSON.stringify(answer))
        return answer.result?.result?.value
      }
      const waitFor = async (expression: string) => {
        for (let attempt = 0; attempt < 100; attempt++) {
          if (await evaluate(`Boolean(${expression})`)) return
          await new Promise(done => setTimeout(done, 100))
        }
        throw new Error(`Timed out waiting for ${expression}: ${await evaluate('document.body.innerText')}; ${browserErrors.join(' | ')}`)
      }
      await send('Page.enable')
      await send('Runtime.enable')
      await send('Log.enable')
      await send('Page.navigate', { url: `http://127.0.0.1:${port}/code-browser` })
      await waitFor(`document.body.innerText.includes('First workspace') && document.querySelector('[aria-label="Capture context"]')`)
      expect(await evaluate(`document.querySelector('[aria-label="Capture context"]').querySelectorAll('input[type="text"]').length`)).toBe(0)
      await waitFor(`document.body.innerText.includes('Review first workspace') && document.body.innerText.includes('native-workspace-terminal')`)
      expect(await evaluate(`document.body.innerText.includes('Review second workspace')`)).toBe(false)
      expect(await evaluate(`getComputedStyle(document.querySelector('[aria-label="Capture context"]')).backgroundColor`)).toBe('rgb(29, 41, 47)')
      await evaluate(`window.__setCodeTheme('light')`)
      await waitFor(`document.querySelector('[aria-label="Capture context"]') &&
        getComputedStyle(document.querySelector('[aria-label="Capture context"]')).backgroundColor === 'rgb(255, 255, 255)'`)
      await evaluate(`window.__setCodeTheme('dark')`)
      await waitFor(`Array.from(document.querySelectorAll('[aria-label="Capture context"] label')).some(label =>
        label.textContent.includes('native-workspace-terminal')) &&
        Array.from(document.querySelectorAll('[aria-label="Capture context"] label')).some(label =>
          label.textContent.includes('Review first workspace'))`)
      await evaluate(`Array.from(document.querySelectorAll('[aria-label="Capture context"] label')).filter(label =>
        label.textContent.includes('native-workspace-terminal') || label.textContent.includes('Review first workspace'))
        .forEach(label => label.querySelector('input[type="checkbox"]').click())`)
      await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Save context').click()`)
      await waitFor(`window.__actionReturned && typeof window.__releaseResponse === 'function'`)
      expect(await evaluate(`window.__captureRequests[0].terminal_ids`)).toEqual([terminal.session_id])
      expect(await evaluate(`window.__captureRequests[0].task_ids`)).toEqual([firstTask.id])
      expect(await evaluate(`window.__switchCode(); document.querySelector('[aria-label="Code workspace"]').getAttribute('data-workspace-state')`)).toBe('loading')
      await waitFor(`document.querySelector('#code-project')?.value === window.__ids.second &&
        Array.from(document.querySelectorAll('[aria-label="Capture context"] label')).some(label =>
          label.textContent.includes('Review second workspace'))`)
      await evaluate(`window.__delayCapture = false; window.__releaseResponse()`)
      await new Promise(done => setTimeout(done, 150))
      expect(await evaluate(`window.__navigations.length`)).toBe(0)
      expect(await evaluate(`document.querySelector('#code-project').value`)).toBe(second.project.id)
      expect(await evaluate(`document.querySelector('[aria-label="Capture context"] button').disabled`)).toBe(false)
      await waitFor(`Array.from(document.querySelectorAll('[aria-label="Capture context"] label')).some(label =>
        label.textContent.includes('native-workspace-terminal')) &&
        Array.from(document.querySelectorAll('[aria-label="Capture context"] label')).some(label =>
          label.textContent.includes('Review second workspace'))`)
      expect(await evaluate(`document.body.innerText.includes('Review first workspace')`)).toBe(false)
      await evaluate(`Array.from(document.querySelectorAll('[aria-label="Capture context"] label')).filter(label =>
        label.textContent.includes('native-workspace-terminal') || label.textContent.includes('Review second workspace'))
        .forEach(label => label.querySelector('input[type="checkbox"]').click())`)
      await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Save context').click()`)
      await waitFor(`window.__navigations.length === 1`)
      expect(await evaluate(`window.__captureRequests[1].project_id`)).toBe(second.project.id)
      expect(await evaluate(`window.__captureRequests[1].terminal_ids`)).toEqual([terminal.session_id])
      expect(await evaluate(`window.__captureRequests[1].task_ids`)).toEqual([secondTask.id])
      const saved = await readSavedContexts()
      expect(saved.some(item => item.project_id === first.project.id && item.task_ids.includes(firstTask.id)
        && item.terminal_ids.includes(terminal.session_id))).toBe(true)
      expect(saved.some(item => item.project_id === second.project.id && item.task_ids.includes(secondTask.id)
        && item.terminal_ids.includes(terminal.session_id))).toBe(true)
      await evaluate(`window.__openIndex()`)
      await waitFor(`document.querySelector('[aria-label="Code workspace"]').getAttribute('data-workspace-state') === 'ready'`)
      expect(await evaluate(`Array.from(document.querySelectorAll('#code-project option')).map(option => option.textContent)`))
        .toEqual(expect.arrayContaining(['First workspace', 'Second workspace']))
      await evaluate(`window.__openNativeProject()`)
      await waitFor(`document.querySelector('[aria-label="Project files"]') &&
        document.querySelector('[aria-label="Open file note.txt"]')`)
      expect(await evaluate(`document.querySelectorAll('main[aria-label="Code workspace"]').length`)).toBe(1)
      await evaluate(`document.querySelector('[aria-label="Open file note.txt"]').click()`)
      await waitFor(`document.querySelector('#code-file-editor')?.value === 'original\\n'`)
      await evaluate(`(() => { const editor = document.querySelector('#code-file-editor');
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(editor, 'saved through Code parent\\n');
        editor.dispatchEvent(new Event('input', { bubbles: true })); })()`)
      await waitFor(`Array.from(document.querySelectorAll('button')).some(button => button.textContent === 'Save file' && !button.disabled)`)
      await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Save file').click()`)
      await waitFor(`document.body.innerText.includes('Saved to the selected project.')`)
      expect(await readFile(join(repo, 'note.txt'), 'utf8')).toBe('saved through Code parent\n')
      await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Open project planning').click()`)
      await waitFor(`document.querySelector('main[aria-label="Native Code and planning project"]') &&
        document.querySelector('[aria-label="Project context"]')`)
      expect(await evaluate(`document.querySelectorAll('main').length`)).toBe(1)
      expect(await evaluate(`document.querySelector('[aria-label="Project files"]') === null`)).toBe(true)
      expect(await evaluate(`window.__navigations.at(-1).record.id`)).toBe(nativeProject.id)
      expect(await evaluate(`window.__navigations.at(-1).placement.subview`)).toBe('/planning')
      await evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Open code workspace').click()`)
      await waitFor(`document.querySelector('[aria-label="Project files"]') &&
        document.querySelector('[aria-label="Open file note.txt"]')`)
      expect(await evaluate(`document.querySelectorAll('main').length`)).toBe(1)
      expect(await evaluate(`window.__navigations.at(-1).record.id`)).toBe(nativeProject.id)
      expect(await evaluate(`window.__navigations.at(-1).placement.subview`)).toBe(undefined)
      expect(await evaluate(`window.__navigations.at(-1).returnTo.destination`)).toBe('chat')
    } finally {
      socket?.close(); chrome?.kill(); await vite.close(); files.kill(); await rm(directory, { recursive: true, force: true })
    }
  }, 90000)
})
