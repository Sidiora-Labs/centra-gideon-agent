import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer as createViteServer, transformWithEsbuild } from 'vite'
import { expect, it } from 'vitest'

async function freePort(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(done => server.close(() => done()))
  return port
}

it('keeps authenticated native media jobs, finished assets, and provider readiness in assistant routes across reload', async () => {
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
    const seeded = await new Promise<{ api_port: number; completed_job_id: string; completed_artifact: { artifact_id: string; version: number } }>((done, fail) => {
      let stdout = '', stderr = ''
      const timer = setTimeout(() => fail(new Error(`Media API start timed out: ${stderr}`)), 20000)
      api.stdout.on('data', chunk => {
        stdout += String(chunk)
        if (stdout.includes('\n')) { clearTimeout(timer); done(JSON.parse(stdout.split('\n')[0])) }
      })
      api.stderr.on('data', chunk => { stderr += String(chunk) })
      api.once('exit', code => { clearTimeout(timer); fail(new Error(`Media API exited ${code}: ${stderr}`)) })
    })
    expect(seeded.completed_artifact.artifact_id).toMatch(/^sketch-/)
    const entry = `
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import { ownerScope } from '/src/shared/auth.web';
      import { parseShellRoute, serializeShellRoute } from '/src/shared/shell/shellRoutes';
      import { createStudioRoute } from '/src/features/studio/studioRoutes';
      import { studioRecordRef } from '/src/features/studio/studioContracts';
      import StudioWorkspace from '/src/features/studio/StudioWorkspace.web';
      window.__mediaDocumentId = crypto.randomUUID();
      const back = { destination: 'chat', sessionId: 'media-conversation', selectionId: 'message-1', scrollY: 64 };
      function App() {
        const [route, setRoute] = React.useState(() => { const parsed = parseShellRoute(location.href, location.origin); return parsed.kind === 'route' ? parsed : createStudioRoute('capabilities/media/jobs', back) });
        const scope = ownerScope(location.origin, { user: 'studio-owner' });
        const navigate = next => { history.pushState(null, '', serializeShellRoute(next)); setRoute(next) };
        return <><button id="open-jobs" onClick={() => navigate(createStudioRoute('capabilities/media/jobs', back,
          studioRecordRef(scope, { kind: 'media.job', id: ${JSON.stringify(seeded.completed_job_id)} }), scope))}>Open jobs</button>
          <button id="open-images" onClick={() => navigate(createStudioRoute('capabilities/media/images', back))}>Open images</button>
          <button id="open-videos" onClick={() => navigate(createStudioRoute('capabilities/media/videos', back))}>Open videos</button>
          <div style={{height:'calc(100vh - 45px)'}}><StudioWorkspace route={route} scope={scope} navigate={navigate}
            returnTo={back} onReturn={() => { document.body.dataset.returned = JSON.stringify(back) }} /></div></>;
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
      ], dedupe: ['react', 'react-dom'], extensions: ['.web.tsx', '.web.ts', '.tsx', '.ts', '.jsx', '.js'] },
      esbuild: { jsx: 'automatic' },
      optimizeDeps: { include: ['react', 'react-dom/client', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'framer-motion', 'lucide-react'] },
      plugins: [{ name: 'media-browser-entry',
        resolveId(id) { if (id === '/media-browser.tsx') return '\0media-browser' },
        async load(id) { if (id === '\0media-browser') return (await transformWithEsbuild(entry, 'media-browser.tsx', { loader: 'tsx', jsx: 'automatic' })).code },
        configureServer(server) { server.middlewares.use('/assistant', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/media-browser.tsx"></script></body></html>')
        }) },
      }], server: { host: '127.0.0.1', port, strictPort: true, fs: { allow: [root] },
        proxy: { '/api': `http://127.0.0.1:${seeded.api_port}` } },
    })
    await vite.listen()
    directory = await mkdtemp(join(tmpdir(), 'gideon-media-browser-'))
    const debugPort = await freePort()
    browser = spawn(process.env.CHROMIUM_BIN || 'chromium', ['--headless', '--no-sandbox', '--disable-gpu',
      '--disable-background-networking', `--remote-debugging-port=${debugPort}`, `--user-data-dir=${directory}`, 'about:blank'])
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
      return response.result.result.value as T
    }
    const waitFor = async (expression: string) => {
      for (let attempt = 0; attempt < 120; attempt++) {
        if (await evaluate<boolean>(expression)) return
        await new Promise(done => setTimeout(done, 100))
      }
      throw new Error(`Browser condition timed out: ${expression}; ${await evaluate<string>('document.body.textContent')}`)
    }
    await send('Page.navigate', { url: `${origin}/assistant` })
    await waitFor("Boolean(document.getElementById('open-jobs'))")
    expect(await evaluate<number>("fetch('/api/capabilities/media/jobs').then(response => response.status)")).toBe(403)
    expect(await evaluate<number>("fetch('/api/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'studio-owner',password:'correct-horse-battery-staple',totp:''})}).then(response => response.status)")).toBe(200)
    await evaluate("document.getElementById('open-jobs').click()")
    await waitFor(`document.body.textContent.includes(${JSON.stringify(seeded.completed_job_id)}) && document.body.textContent.includes('completed')`)
    expect(await evaluate<boolean>(`fetch('/api/artifacts/${encodeURIComponent(seeded.completed_artifact.artifact_id)}/raw?version=${seeded.completed_artifact.version}').then(async response => response.ok && (await response.arrayBuffer()).byteLength > 0)`)).toBe(true)
    expect(await evaluate<string>('location.hash')).toBe('')
    const documentId = await evaluate<string>('window.__mediaDocumentId')
    await send('Page.reload')
    await waitFor(`window.__mediaDocumentId !== ${JSON.stringify(documentId)} && document.body.textContent.includes('completed')`)
    await evaluate("document.getElementById('open-images').click()")
    await waitFor("Boolean(document.querySelector('[aria-label=\"Image generation\"]'))")
    const imageCaps = await evaluate<{ available: boolean }>("fetch('/api/capabilities/media/images').then(response => response.json())")
    await waitFor("document.body.textContent.includes('Selected:')")
    expect(await evaluate<boolean>("[...document.querySelectorAll('button')].some(button => button.textContent.includes('Queue image generation') && button.disabled)")).toBe(true)
    if (!imageCaps.available) expect(await evaluate<string>('document.body.textContent')).toContain('Provider unavailable')
    await evaluate("document.getElementById('open-videos').click()")
    await waitFor("Boolean(document.querySelector('[aria-label=\"Video generation\"]'))")
    expect(await evaluate<string>('location.hash')).toBe('')
  } finally {
    socket?.close()
    browser?.kill('SIGTERM')
    await vite?.close()
    api.kill('SIGTERM')
    if (directory) await rm(directory, { recursive: true, force: true, maxRetries: 12, retryDelay: 100 })
  }
}, 60000)
