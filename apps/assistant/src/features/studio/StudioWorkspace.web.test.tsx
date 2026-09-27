import * as React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer as createViteServer, transformWithEsbuild } from 'vite'
import { describe, expect, it } from 'vitest'
import { ownerScope } from '../../shared/auth.web'
import { parseShellRoute, serializeShellRoute } from '../../shared/shell/shellRoutes'
import { DESTINATIONS } from '../discovery/destinations'
import StudioWorkspace from './StudioWorkspace.web'
import { studioModules } from './moduleDefinitions.web'
import { sameStudioOwner, studioRecordRef } from './studioContracts'
import { createStudioRoute, resolveStudioRoute, studioDestination, studioRouteRecord, studioTarget } from './studioRoutes'

const scope = ownerScope('https://gideon.example', { user: 'owner-1' })

describe('Studio workspace routes', () => {
  it('covers every labelled Studio and Create destination with a full-size route and an honest target state', () => {
    const labelled = DESTINATIONS.filter(entry => entry.owner === 'studio')
    expect(labelled.length).toBeGreaterThan(35)
    for (const entry of labelled) {
      const route = createStudioRoute(entry.id)
      expect(studioDestination(route)?.id).toBe(entry.id)
      expect(route.view).toBe('workspace')
      expect(studioModules[0].matches(route)).toBe(true)
      const target = studioTarget(route)
      if (entry.id === 'design' || entry.id === 'capabilities/experience/travel') expect(target).toBeNull()
      else expect(['creative', 'media', 'music', 'experience']).toContain(target?.area)
    }
    expect(() => createStudioRoute('capabilities/media/imaginary')).toThrow()
  })

  it('round trips native identity, provenance, readiness, artifact and conversation return context', () => {
    const native = studioRecordRef(scope, {
      kind: 'creative.work', id: 'work/42', revision: 7,
      source: { conversationId: 'conversation-3', runId: 'run-9' },
      providerCapability: 'writing', job: { id: 'job-2', state: 'running' },
      artifact: { id: 'artifact-8', version: 2, available: false },
    })
    const route = createStudioRoute('capabilities/creative/works', {
      destination: 'chat', sessionId: 'conversation-3', selectionId: 'message-5', scrollY: 280,
    }, native, scope)
    const parsed = parseShellRoute(serializeShellRoute(route), scope.runtimeOrigin)
    expect(parsed.kind).toBe('route')
    if (parsed.kind !== 'route') return
    expect(parsed.record).toEqual({ kind: 'creative.work', id: 'work/42' })
    expect(parsed.placement?.query).toEqual({
      revision: '7', sourceConversation: 'conversation-3', sourceRun: 'run-9',
      providerCapability: 'writing', jobId: 'job-2', jobStatus: 'running',
      artifactId: 'artifact-8', artifactVersion: '2', artifactAvailable: 'false',
    })
    expect(parsed.returnTo).toMatchObject({ destination: 'chat', sessionId: 'conversation-3', selectionId: 'message-5', scrollY: 280 })
    expect(studioRouteRecord(parsed, scope)).toEqual(native)
    expect(sameStudioOwner(scope, native)).toBe(true)
    const otherOwner = ownerScope(scope.runtimeOrigin, { user: 'owner-2' })
    expect(sameStudioOwner(otherOwner, native)).toBe(false)
    expect(() => createStudioRoute('capabilities/creative/works', undefined, native, otherOwner)).toThrow()
    expect(() => createStudioRoute('capabilities/media/sketches', undefined, native, scope)).toThrow()
  })

  it('rejects an unsupported native ref and exposes a full-width, keyboard-returnable frame', async () => {
    const route = createStudioRoute('capabilities/media/images')
    const unsupported = { ...route, record: { kind: 'invented', id: 'record-1' } }
    expect(await resolveStudioRoute(unsupported, scope)).toBe('unavailable')
    const html = renderToStaticMarkup(<StudioWorkspace route={route} scope={scope}
      navigate={() => {}} onReturn={() => {}} returnTo={{ destination: 'chat', sessionId: 'conversation-3' }} />)
    expect(html).toContain('data-workspace-mode="full"')
    expect(html).toContain('data-workspace-state="loading"')
    expect(html).toContain('aria-label="Back"')
    expect(html).toContain('Image generation')
    expect(html).not.toContain('<iframe')
  })
})

async function freePort(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(done => server.close(() => done()))
  return port
}

it('opens a seeded native artifact in one React root, returns to its conversation, and keeps console hash navigation', async () => {
  const root = resolve(process.cwd(), '../..')
  const port = await freePort()
  const origin = `http://127.0.0.1:${port}`
  const api = spawn(process.env.GIDEON_TEST_PYTHON || 'python3',
    [join(root, 'apps/assistant/test-support/studio_server.py'), origin],
    { env: { ...process.env, PYTHONPATH: join(root, 'runtime') } })
  let vite: Awaited<ReturnType<typeof createViteServer>> | undefined
  let browser: ChildProcessWithoutNullStreams | undefined
  let socket: WebSocket | undefined
  let directory: string | undefined
  try {
    const seeded = await new Promise<{ api_port: number; artifact_id: string; version: number }>((done, fail) => {
      let stdout = '', stderr = ''
      const timer = setTimeout(() => fail(new Error(`Studio API start timed out: ${stderr}`)), 15000)
      api.stdout.on('data', chunk => {
        stdout += String(chunk)
        if (stdout.includes('\n')) { clearTimeout(timer); done(JSON.parse(stdout.split('\n')[0])) }
      })
      api.stderr.on('data', chunk => { stderr += String(chunk) })
      api.once('exit', code => { clearTimeout(timer); fail(new Error(`Studio API exited ${code}: ${stderr}`)) })
    })
    const entry = `
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import { ownerScope } from '/src/shared/auth.web';
      import { createShellRoute, serializeShellRoute } from '/src/shared/shell/shellRoutes';
      import { studioRecordRef } from '/src/features/studio/studioContracts';
      import { createStudioRoute, resolveStudioRoute } from '/src/features/studio/studioRoutes';
      import StudioWorkspace from '/src/features/studio/StudioWorkspace.web';
      import LibraryPage from '/@fs/${join(root, 'apps/console/src/features/capabilities/media/LibraryPage.tsx')}';
      const back = { destination: 'chat', sessionId: 'conversation-studio-test', selectionId: 'message-7', scrollY: 260 };
      function App() {
        const [mode, setMode] = React.useState('start');
        const [owner, setOwner] = React.useState('studio-owner');
        const scope = ownerScope(location.origin, { user: owner });
        const [route, setRoute] = React.useState(createStudioRoute('capabilities/media/library', back));
        const [returned, setReturned] = React.useState(null);
        React.useLayoutEffect(() => {
          const detail = Boolean(document.querySelector('[aria-label="Media details"]'));
          if (mode === 'assistant') window.__studioDetailAtCommit = detail;
          if (mode === 'controlled-empty') window.__controlledDetailAtCommit = detail;
        }, [mode, owner, route]);
        const navigate = next => { history.pushState(null, '', serializeShellRoute(next)); setRoute(next); };
        return <><nav><button id="open-assistant" onClick={() => { setMode('assistant'); navigate(createStudioRoute('capabilities/media/library', back)); }}>Open Studio</button>
          <button id="open-seeded" onClick={() => { setMode('assistant'); navigate(createStudioRoute('capabilities/media/library', back,
            studioRecordRef(scope, { kind: 'media.artifact', id: ${JSON.stringify(seeded.artifact_id)}, artifact: { id: ${JSON.stringify(seeded.artifact_id)}, version: ${seeded.version}, available: true } }), scope)); }}>Open seeded result</button>
          <button id="open-missing" onClick={() => navigate(createStudioRoute('capabilities/media/library', back,
            studioRecordRef(scope, { kind: 'media.artifact', id: 'missing-artifact' }), scope))}>Open missing result</button>
          <button id="switch-owner" onClick={() => setOwner('other-owner')}>Switch owner</button>
          <button id="restore-owner" onClick={() => setOwner('studio-owner')}>Restore owner</button>
          <button id="open-console" onClick={() => { location.hash = '/capabilities/media?view=library'; setMode('console'); }}>Open console library</button>
          <button id="controlled-empty" onClick={() => setMode('controlled-empty')}>Controlled empty library</button>
          <button id="wrong-owner" onClick={() => { void resolveStudioRoute(createStudioRoute('capabilities/media/library'),
            ownerScope(location.origin, {user:'other-owner'})).then(result => { document.getElementById('owner-result').textContent = result; }); }}>Check wrong owner</button></nav>
          <output id="owner-result" />
          {mode === 'assistant' ? <div style={{ height: 'calc(100vh - 44px)' }}><StudioWorkspace route={route} scope={scope} navigate={navigate}
            returnTo={route.returnTo} onReturn={() => { setReturned(route.returnTo); navigate(createShellRoute('chat', { sessionId: route.returnTo?.sessionId })); setMode('returned'); }} /></div>
            : mode === 'console' ? <LibraryPage />
              : mode === 'controlled-empty' ? <LibraryPage artifactId="" onSelectArtifact={() => {}} />
              : mode === 'returned' ? <output id="return-context">{JSON.stringify(returned)}</output> : null}
        </>;
      }
      createRoot(document.getElementById('root')).render(<App />);
    `
    const modules = join(root, 'apps/assistant/node_modules')
    vite = await createViteServer({ configFile: false, root: join(root, 'apps/assistant'),
      resolve: { alias: [
        { find: 'react', replacement: join(modules, 'react') },
        { find: 'react-dom', replacement: join(modules, 'react-dom') },
        { find: 'framer-motion', replacement: join(modules, 'framer-motion') },
        { find: 'lucide-react', replacement: join(modules, 'lucide-react') },
        { find: /^react-native$/, replacement: 'react-native-web' },
      ], dedupe: ['react', 'react-dom'],
        extensions: ['.web.tsx', '.web.ts', '.tsx', '.ts', '.jsx', '.js'] },
      esbuild: { jsx: 'automatic' },
      optimizeDeps: { include: ['react', 'react-dom/client', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'framer-motion', 'lucide-react'] },
      plugins: [{ name: 'studio-browser-entry',
        resolveId(id) { if (id === '/studio-browser.tsx') return '\0studio-browser' },
        async load(id) { if (id === '\0studio-browser') return (await transformWithEsbuild(entry, 'studio-browser.tsx', { loader: 'tsx', jsx: 'automatic' })).code },
        configureServer(server) { server.middlewares.use('/assistant', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0"><div id="root"></div><script type="module" src="/studio-browser.tsx"></script></body></html>')
        }) },
      }],
      server: { host: '127.0.0.1', port, strictPort: true, fs: { allow: [root] },
        proxy: { '/api': `http://127.0.0.1:${seeded.api_port}` } },
    })
    await vite.listen()
    directory = await mkdtemp(join(tmpdir(), 'gideon-studio-browser-'))
    const debugPort = await freePort()
    browser = spawn(process.env.CHROMIUM_BIN || 'chromium', ['--headless', '--no-sandbox', '--disable-gpu',
      '--disable-background-networking', `--remote-debugging-port=${debugPort}`,
      `--user-data-dir=${directory}`, 'about:blank'])
    let target: { webSocketDebuggerUrl: string } | undefined
    for (let attempt = 0; attempt < 100; attempt++) {
      try {
        const pages = await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json() as Array<{ type: string; webSocketDebuggerUrl: string }>
        target = pages.find(page => page.type === 'page')
        if (target) break
      } catch { await new Promise(done => setTimeout(done, 100)) }
    }
    if (!target) throw new Error('Chromium page did not start')
    socket = new WebSocket(target.webSocketDebuggerUrl)
    await new Promise<void>((done, fail) => { socket!.addEventListener('open', () => done(), { once: true }); socket!.addEventListener('error', () => fail(new Error('Chromium socket failed')), { once: true }) })
    let sequence = 0
    const pending = new Map<number, (value: any) => void>()
    socket.addEventListener('message', event => {
      const message = JSON.parse(String(event.data))
      if (message.id && pending.has(message.id)) { pending.get(message.id)!(message); pending.delete(message.id) }
    })
    const send = (method: string, params: Record<string, unknown> = {}) => new Promise<any>(done => {
      const id = ++sequence
      pending.set(id, done)
      socket!.send(JSON.stringify({ id, method, params }))
    })
    const evaluate = async <T,>(expression: string): Promise<T> => {
      const response = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
      if (response.result?.exceptionDetails) throw new Error(JSON.stringify(response.result.exceptionDetails))
      if (!response.result?.result) throw new Error(`Browser evaluation did not complete: ${JSON.stringify(response)}`)
      return response.result?.result?.value as T
    }
    const waitFor = async (expression: string) => {
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await evaluate<boolean>(expression)) return
        await new Promise(done => setTimeout(done, 100))
      }
      throw new Error(`Browser condition timed out: ${expression}`)
    }
    const holdNextFetch = async (path: string) => {
      await evaluate(`(() => { const original = window.fetch; const path = ${JSON.stringify(path)};
        window.__studioRelease = undefined;
        window.fetch = (input, options) => {
          const url = typeof input === 'string' ? input : input.url;
          if (new URL(url, location.origin).pathname !== path) return original(input, options);
          window.fetch = original;
          return new Promise(resolve => { window.__studioRelease = () => resolve(original(input, options)); });
        };
      })()`)
    }
    await send('Page.navigate', { url: `${origin}/assistant` })
    await waitFor("Boolean(document.getElementById('open-assistant'))")
    expect(await evaluate<number>("fetch('/api/capabilities/media/library').then(response => response.status)")).toBe(403)
    expect(await evaluate<number>(`fetch('/api/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'studio-owner',password:'correct-horse-battery-staple',totp:''})}).then(response => response.status)`)).toBe(200)
    await evaluate("document.getElementById('wrong-owner').click()")
    await waitFor("document.getElementById('owner-result').textContent === 'denied'")
    await evaluate("document.getElementById('open-assistant').click()")
    await waitFor("document.body.textContent.includes('1 matching artifacts')")
    await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent === 'Studio harbor image').click()")
    await waitFor("Boolean(document.querySelector('[aria-label=\"Media details\"]'))")
    expect(await evaluate<string>('location.hash')).toBe('')
    expect(await evaluate<string>('location.pathname')).toBe('/assistant/apps')
    await evaluate("document.querySelector('.gideon-workspace-back').click()")
    await waitFor("Boolean(document.getElementById('return-context'))")
    expect(await evaluate<string>("document.getElementById('return-context').textContent")).toContain('"selectionId":"message-7","scrollY":260')
    expect(await evaluate<string>('location.pathname')).toBe('/assistant/chat')
    expect(await evaluate<string>('location.search')).toContain('conversation-studio-test')
    await evaluate("document.getElementById('open-seeded').click()")
    await waitFor("Boolean(document.querySelector('[aria-label=\"Media details\"]'))")
    expect(await evaluate<string>('location.hash')).toBe('')
    await holdNextFetch('/api/capabilities/media/library/missing-artifact')
    await evaluate("document.getElementById('open-missing').click()")
    await waitFor("Boolean(window.__studioRelease)")
    expect(await evaluate<boolean>('window.__studioDetailAtCommit')).toBe(false)
    expect(await evaluate<boolean>("Boolean(document.querySelector('[aria-label=\"Media details\"]'))")).toBe(false)
    expect(await evaluate<string>("document.querySelector('[data-workspace-state]').getAttribute('data-workspace-state')")).toBe('loading')
    await evaluate('window.__studioRelease()')
    await waitFor("document.querySelector('[data-workspace-state]').getAttribute('data-workspace-state') === 'empty'")
    await evaluate("document.getElementById('open-seeded').click()")
    await waitFor("Boolean(document.querySelector('[aria-label=\"Media details\"]'))")
    await holdNextFetch('/api/auth/session')
    await evaluate("document.getElementById('switch-owner').click()")
    await waitFor("Boolean(window.__studioRelease)")
    expect(await evaluate<boolean>('window.__studioDetailAtCommit')).toBe(false)
    expect(await evaluate<boolean>("Boolean(document.querySelector('[aria-label=\"Media details\"]'))")).toBe(false)
    expect(await evaluate<string>("document.querySelector('[data-workspace-state]').getAttribute('data-workspace-state')")).toBe('loading')
    await evaluate('window.__studioRelease()')
    await waitFor("document.querySelector('[data-workspace-state]').getAttribute('data-workspace-state') === 'denied'")
    await evaluate("document.getElementById('restore-owner').click()")
    await waitFor("Boolean(document.querySelector('[aria-label=\"Media details\"]'))")
    await evaluate("document.getElementById('open-console').click()")
    await waitFor("document.body.textContent.includes('1 matching artifacts')")
    await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent === 'Studio harbor image').click()")
    await waitFor("location.hash.includes('artifact=studio-harbor-image')")
    expect(await evaluate<string>('location.hash')).toBe('#/capabilities/media?view=library&artifact=studio-harbor-image')
    await evaluate("document.getElementById('controlled-empty').click()")
    await waitFor("document.body.textContent.includes('1 matching artifacts')")
    expect(await evaluate<boolean>('window.__controlledDetailAtCommit')).toBe(false)
    expect(await evaluate<boolean>("Boolean(document.querySelector('[aria-label=\"Media details\"]'))")).toBe(false)
    expect(await evaluate<string>('location.hash')).toBe('#/capabilities/media?view=library&artifact=studio-harbor-image')
  } finally {
    socket?.close()
    browser?.kill('SIGTERM')
    await vite?.close()
    api.kill('SIGTERM')
    if (directory) await rm(directory, { recursive: true, force: true })
  }
}, 45000)
