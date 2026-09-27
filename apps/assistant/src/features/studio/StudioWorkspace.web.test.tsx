import * as React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
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
import { createSlidesRoute, createStudioRoute, isSlidesRoute, resolveStudioRoute, studioDestination, studioRouteRecord, studioTarget } from './studioRoutes'

const scope = ownerScope('https://gideon.example', { user: 'owner-1' })

describe('Studio workspace routes', () => {
  it('registers Slides under design and round trips its artifact and return context', () => {
    const back = { destination: 'chat' as const, sessionId: 'conversation-3', selectionId: 'message-5', scrollY: 280 }
    const ref = studioRecordRef(scope, { kind: 'artifact', id: 'deck-42' })
    const route = createSlidesRoute(back, ref, scope)
    const parsed = parseShellRoute(serializeShellRoute(route), scope.runtimeOrigin)
    expect(parsed.kind).toBe('route')
    if (parsed.kind !== 'route') return
    expect(studioModules[0].matches(parsed)).toBe(true)
    expect(isSlidesRoute(parsed)).toBe(true)
    expect(parsed.placement?.subview).toBe('/slides')
    expect(parsed.record).toEqual({ kind: 'artifact', id: 'deck-42' })
    expect(parsed.returnTo).toMatchObject(back)
    expect(studioRouteRecord(parsed, scope)).toEqual(ref)
    expect(() => createStudioRoute('design', back, ref, scope)).toThrow()
  })
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

const slidesNativeServer = String.raw`
import asyncio, json, os, sys, tempfile, time
from pathlib import Path
from aiohttp import web

async def main(origin):
    with tempfile.TemporaryDirectory(prefix='gideon-studio-slides-') as directory:
        home = Path(directory)
        os.environ['GIDEON_HOME'] = str(home)
        from gideon.core.config.loader import AppConfig
        from gideon.engine.session import ConversationDirectory
        from gideon.interfaces.dashboard import token_auth
        from gideon.interfaces.dashboard.state import ConsoleState
        from gideon.interfaces.dashboard.handlers import auth
        from gideon.security.auth import credentials
        from gideon.workspace.artifacts import registry
        from gideon.workspace.artifacts.native import NativeArtifactProvider
        from gideon.workspace.artifacts.handlers import register_artifact_routes
        (home / 'config.json').write_text(json.dumps({'auth': {'login_enabled': True}, 'dashboard': {'document_editing': True}}), encoding='utf-8')
        credentials.set_password('slides-owner', 'correct-horse-battery-staple')
        token_auth.use_ephemeral_secret()
        registry.register_provider(NativeArtifactProvider(home / 'artifacts'))
        app = web.Application(middlewares=[token_auth.token_auth_middleware(port=10000)])
        app['port'] = 10000
        app['allowed_origins'] = {origin}
        app['state'] = ConsoleState(sessions=ConversationDirectory(AppConfig.load()), start_time=time.time(), owner_id='slides-owner')
        app.router.add_post('/api/auth/login', auth.api_auth_login)
        app.router.add_get('/api/auth/session', auth.api_auth_session)
        register_artifact_routes(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        print(json.dumps({'api_port': site._server.sockets[0].getsockname()[1]}), flush=True)
        try: await asyncio.Event().wait()
        finally: await runner.cleanup()

asyncio.run(main(sys.argv[1]))
`

it('opens Slides through the registered Studio module, reloads its native artifact route, and returns to chat', async () => {
  const root = resolve(process.cwd(), '../..')
  const port = await freePort()
  const origin = `http://127.0.0.1:${port}`
  const api = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', slidesNativeServer, origin],
    { env: { ...process.env, PYTHONPATH: join(root, 'runtime') } })
  let vite: Awaited<ReturnType<typeof createViteServer>> | undefined
  let browser: ChildProcessWithoutNullStreams | undefined
  let socket: WebSocket | undefined
  let directory: string | undefined
  let assetDirectory: string | undefined
  try {
    const started = await new Promise<{ api_port: number }>((done, fail) => {
      let stdout = '', stderr = ''
      const timer = setTimeout(() => fail(new Error(`Slides API start timed out: ${stderr}`)), 15000)
      api.stdout.on('data', chunk => {
        stdout += String(chunk)
        if (stdout.includes('\n')) { clearTimeout(timer); done(JSON.parse(stdout.split('\n')[0])) }
      })
      api.stderr.on('data', chunk => { stderr += String(chunk) })
      api.once('exit', code => { clearTimeout(timer); fail(new Error(`Slides API exited ${code}: ${stderr}`)) })
    })
    const entry = `
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import { ownerScope } from '/src/shared/auth.web';
      import { createShellRoute, parseShellRoute, serializeShellRoute } from '/src/shared/shell/shellRoutes';
      import { studioModules } from '/src/features/studio/moduleDefinitions.web';
      import { createSlidesRoute, createStudioRoute } from '/src/features/studio/studioRoutes';
      const back = { destination: 'chat', sessionId: 'conversation-slides', selectionId: 'message-4', scrollY: 360 };
      const Studio = React.lazy(studioModules[0].load);
      function App() {
        const scope = React.useMemo(() => ownerScope(location.origin, { user: 'slides-owner' }), []);
        const [route, setRoute] = React.useState(() => {
          const parsed = parseShellRoute(location.href, location.origin);
          return parsed.kind === 'route' && studioModules[0].matches(parsed) ? parsed : createShellRoute('chat');
        });
        const [returned, setReturned] = React.useState(null);
        const navigate = next => { history.pushState(null, '', serializeShellRoute(next)); setRoute(next); };
        return <><button id="open-design" onClick={() => navigate(createStudioRoute('design', back))}>Open Design</button>
          {route.destination === 'apps' ? <div className="gideon-trusted-module full" style={{height:'100vh'}}><React.Suspense fallback={<p>Loading Studio</p>}>
            <Studio route={route} scope={scope} navigate={navigate} returnTo={route.returnTo}
              onReturn={() => { setReturned(route.returnTo); navigate(createShellRoute('chat', {sessionId:route.returnTo?.sessionId})); }} />
          </React.Suspense></div> : <output id="return-context">{JSON.stringify(returned)}</output>}
        </>;
      }
      createRoot(document.getElementById('root')).render(<App />);
    `
    assetDirectory = await mkdtemp(join(tmpdir(), 'gideon-studio-slides-assets-'))
    const assets = spawn(process.execPath, [join(root, 'apps/assistant/tooling/buildTrustedWebAssets.mjs'), '--out-dir', assetDirectory],
      { cwd: join(root, 'apps/assistant') })
    await new Promise<void>((done, fail) => {
      let stderr = ''
      assets.stderr.on('data', chunk => { stderr += String(chunk) })
      assets.once('exit', code => code === 0 ? done() : fail(new Error(`Studio styles exited ${code}: ${stderr}`)))
      assets.once('error', fail)
    })
    const modules = join(root, 'apps/assistant/node_modules')
    vite = await createViteServer({ configFile: false, root: join(root, 'apps/assistant'),
      resolve: { alias: [
        { find: 'react', replacement: join(modules, 'react') },
        { find: 'react-dom', replacement: join(modules, 'react-dom') },
        { find: 'framer-motion', replacement: join(modules, 'framer-motion') },
        { find: 'lucide-react', replacement: join(modules, 'lucide-react') },
        { find: /^react-native$/, replacement: 'react-native-web' },
      ], dedupe: ['react', 'react-dom'], extensions: ['.web.tsx', '.web.ts', '.tsx', '.ts', '.jsx', '.js'] },
      esbuild: { jsx: 'automatic' },
      optimizeDeps: { include: ['react', 'react-dom/client', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'framer-motion', 'lucide-react'] },
      plugins: [{ name: 'studio-slides-entry',
        resolveId(id) { if (id === '/studio-slides.tsx') return '\0studio-slides' },
        async load(id) { if (id === '\0studio-slides') return (await transformWithEsbuild(entry, 'studio-slides.tsx', { loader: 'tsx', jsx: 'automatic' })).code },
        configureServer(server) { server.middlewares.use('/studio-assets', async (request, response, next) => {
          const asset = request.url?.slice(1) ?? ''
          if (!/^(gideon-console\.css|fonts\/[a-z-]+\.woff2)$/.test(asset)) return next()
          try {
            response.setHeader('Content-Type', asset.endsWith('.css') ? 'text/css; charset=utf-8' : 'font/woff2')
            response.end(await readFile(join(assetDirectory!, asset)))
          } catch (error) { next(error) }
        }); server.middlewares.use('/assistant', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/studio-assets/gideon-console.css"></head><body style="margin:0"><div id="root"></div><script type="module" src="/studio-slides.tsx"></script></body></html>')
        }) },
      }],
      server: { host: '127.0.0.1', port, strictPort: true, fs: { allow: [root] },
        proxy: { '/api': `http://127.0.0.1:${started.api_port}` } },
    })
    await vite.listen()
    directory = await mkdtemp(join(tmpdir(), 'gideon-studio-slides-browser-'))
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
    const capture = async (name: string) => {
      const evidenceDirectory = process.env.GIDEON_STUDIO_EVIDENCE_DIR
      if (!evidenceDirectory) return
      const response = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
      if (!response.result?.data) throw new Error(`Studio screenshot failed: ${name}`)
      await mkdir(evidenceDirectory, { recursive: true })
      await writeFile(join(evidenceDirectory, `${name}.png`), Buffer.from(response.result.data, 'base64'))
    }
    const evaluate = async <T,>(expression: string): Promise<T> => {
      const response = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
      if (response.result?.exceptionDetails) throw new Error(JSON.stringify(response.result.exceptionDetails))
      if (!response.result?.result) throw new Error(`Browser evaluation did not complete: ${JSON.stringify(response)}`)
      return response.result.result.value as T
    }
    const waitFor = async (expression: string) => {
      for (let attempt = 0; attempt < 120; attempt++) {
        if (await evaluate<boolean>(expression)) return
        await new Promise(done => setTimeout(done, 100))
      }
      throw new Error(`Browser condition timed out: ${expression}`)
    }
    await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false })
    await send('Page.navigate', { url: `${origin}/assistant` })
    await waitFor("Boolean(document.getElementById('open-design'))")
    expect(await evaluate<number>("fetch('/api/artifacts?kind=pptx').then(response => response.status)")).toBe(403)
    expect(await evaluate<number>(`fetch('/api/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'slides-owner',password:'correct-horse-battery-staple',totp:''})}).then(response => response.status)`)).toBe(200)
    await evaluate("document.getElementById('open-design').click()")
    await waitFor("Boolean([...document.querySelectorAll('button')].find(button => button.textContent === 'Open Slides'))")
    await waitFor("getComputedStyle(document.querySelector('section.bg-surface')).backgroundColor === 'rgb(32, 32, 36)'")
    await capture('design-desktop')
    await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent === 'Open Slides').click()")
    await waitFor("Boolean(document.querySelector('[aria-label=\"New presentation outline\"]'))")
    await capture('slides-create-desktop')
    await evaluate(`(() => { const input = document.querySelector('[aria-label="New presentation name"]');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'Quarterly review');
      input.dispatchEvent(new Event('input', {bubbles:true}));
      const outline = document.querySelector('[aria-label="New presentation outline"]');
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(outline, '## Results\\n- Verified');
      outline.dispatchEvent(new Event('input', {bubbles:true})); })()`)
    await waitFor("Boolean([...document.querySelectorAll('button')].find(button => button.textContent.includes('Create presentation') && !button.disabled))")
    await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent.includes('Create presentation')).click()")
    await waitFor("Boolean(document.querySelector('[aria-label=\"Slide title\"]'))")
    const routeUrl = await evaluate<string>('location.href')
    const routeQuery = new URL(routeUrl).searchParams
    expect(routeQuery.get('placement')).toBe('design')
    expect(routeQuery.get('subview')).toBe('/slides')
    expect(routeQuery.get('recordKind')).toBe('artifact')
    expect(routeQuery.get('recordId')).toBe('quarterly-review')
    expect(routeQuery.get('from')).toBe('chat')
    expect(await evaluate<string>('location.hash')).toBe('')
    await capture('slides-created-desktop')
    await evaluate('window.__studioDocumentBeforeReload = true')
    await send('Page.reload', { ignoreCache: true })
    let reloaded = false
    for (let attempt = 0; attempt < 120; attempt++) {
      try {
        reloaded = await evaluate<boolean>("!window.__studioDocumentBeforeReload && document.readyState === 'complete' && new URLSearchParams(location.search).get('recordId') === 'quarterly-review' && document.querySelector('[aria-label=\"Slide title\"]')?.value === 'Results'")
      } catch (error) {
        if (!/Execution context was destroyed|Cannot find context with specified id|Cannot find context with id/.test(String(error))) throw error
      }
      if (reloaded) break
      await new Promise(done => setTimeout(done, 100))
    }
    expect(reloaded).toBe(true)
    expect(await evaluate<string>("document.querySelector('[aria-label=\"Slide title\"]').value")).toBe('Results')
    await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
    expect(await evaluate<boolean>(`(() => {
      const title = document.querySelector('[aria-label="Slides workspace"] header h2').parentElement.getBoundingClientRect();
      const picker = document.querySelector('[aria-label="Slides workspace"] header label').getBoundingClientRect();
      return title.width >= 250 && picker.top >= title.bottom - 1;
    })()`)).toBe(true)
    await capture('slides-reloaded-narrow')
    expect(await evaluate<boolean>('document.documentElement.scrollWidth <= window.innerWidth + 1')).toBe(true)
    await evaluate("document.querySelector('.gideon-workspace-back').click()")
    await waitFor("Boolean(document.getElementById('return-context'))")
    expect(await evaluate<string>("document.getElementById('return-context').textContent")).toContain('"selectionId":"message-4","scrollY":360')
    expect(await evaluate<string>('location.pathname')).toBe('/assistant/chat')
  } finally {
    socket?.close()
    browser?.kill('SIGTERM')
    await vite?.close()
    api.kill('SIGTERM')
    if (directory) await rm(directory, { recursive: true, force: true })
    if (assetDirectory) await rm(assetDirectory, { recursive: true, force: true })
  }
}, 120000)
