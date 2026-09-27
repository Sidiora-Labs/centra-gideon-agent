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
  const key = process.env.GIDEON_TEST_IMAGE_API_KEY
  if (!key) throw new Error('The isolated image journey requires a provider credential')
  const api = spawn(process.env.GIDEON_TEST_PYTHON || 'python3',
    [join(root, 'apps/assistant/test-support/studio_server.py'), origin],
    { env: { PATH: process.env.PATH || '', PYTHONPATH: join(root, 'runtime'), GIDEON_TEST_IMAGE_PROVIDER: '1', GIDEON_TEST_IMAGE_API_KEY: key } })
  let vite: Awaited<ReturnType<typeof createViteServer>> | undefined
  let browser: ChildProcessWithoutNullStreams | undefined
  let socket: WebSocket | undefined
  let directory: string | undefined
  try {
    const seeded = await new Promise<{ api_port: number; sketch_id: string; completed_job_id: string; completed_artifact: { artifact_id: string; version: number } }>((done, fail) => {
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
        const navigate = next => { window.__mediaNavigations = (window.__mediaNavigations || 0) + 1; history.pushState(null, '', serializeShellRoute(next)); setRoute(next) };
        return <><button id="open-jobs" onClick={() => navigate(createStudioRoute('capabilities/media/jobs', back,
          studioRecordRef(scope, { kind: 'media.job', id: ${JSON.stringify(seeded.completed_job_id)} }), scope))}>Open jobs</button>
          <button id="open-images" onClick={() => navigate(createStudioRoute('capabilities/media/images', back))}>Open images</button>
          <button id="open-sketches" onClick={() => navigate(createStudioRoute('capabilities/media/sketches', back,
            studioRecordRef(scope, { kind: 'media.sketch', id: ${JSON.stringify(seeded.sketch_id)} }), scope))}>Open sketches</button>
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
    const waitFor = async (expression: string, timeoutMs = 12000) => {
      const deadline = Date.now() + timeoutMs
      while (Date.now() < deadline) {
        if (await evaluate<boolean>(expression)) return
        await new Promise(done => setTimeout(done, 200))
      }
      throw new Error(`Browser condition timed out: ${expression}; ${await evaluate<string>('document.body.textContent')}`)
    }
    await send('Page.navigate', { url: `${origin}/assistant` })
    await waitFor("Boolean(document.getElementById('open-jobs'))")
    expect(await evaluate<number>("fetch('/api/capabilities/media/jobs').then(response => response.status)")).toBe(403)
    expect(await evaluate<number>("fetch('/api/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'studio-owner',password:'correct-horse-battery-staple',totp:''})}).then(response => response.status)")).toBe(200)
    await evaluate("document.getElementById('open-jobs').click()")
    await waitFor(`Boolean([...document.querySelectorAll('article')].find(card => card.textContent.includes(${JSON.stringify(seeded.completed_job_id)}) && card.querySelector('[role="status"]')?.textContent.includes('succeeded')))`)
    expect(await evaluate<boolean>(`fetch('/api/artifacts/${encodeURIComponent(seeded.completed_artifact.artifact_id)}/raw?version=${seeded.completed_artifact.version}').then(async response => response.ok && (await response.arrayBuffer()).byteLength > 0)`)).toBe(true)
    expect(await evaluate<string>('location.hash')).toBe('')
    const documentId = await evaluate<string>('window.__mediaDocumentId')
    await send('Page.reload')
    await waitFor(`window.__mediaDocumentId && window.__mediaDocumentId !== ${JSON.stringify(documentId)} && Boolean([...document.querySelectorAll('article')].find(card => card.textContent.includes(${JSON.stringify(seeded.completed_job_id)}) && card.querySelector('[role="status"]')?.textContent.includes('succeeded')))`)
    await evaluate("document.getElementById('open-images').click()")
    await waitFor("Boolean(document.querySelector('[aria-label=\"Image generation\"]'))")
    const imageCaps = await evaluate<{ available: boolean; selection: string; models: { name: string }[] }>("fetch('/api/capabilities/media/images').then(response => response.json())")
    expect(imageCaps.available).toBe(true)
    expect(imageCaps.selection).toBe('OpenAI:gpt-image-1')
    expect(imageCaps.models.map(model => model.name)).toContain('gpt-image-1')
    await waitFor("document.body.textContent.includes('Selected: OpenAI:gpt-image-1') && Boolean(document.querySelector('textarea'))")
    await evaluate(`(() => {
      window.__imageRequest = null;
      const original = window.fetch.bind(window);
      window.fetch = (input, init) => {
        if (String(input) === '/api/capabilities/media/images' && init?.method === 'POST') window.__imageRequest = JSON.parse(init.body);
        return original(input, init);
      };
      const field = document.querySelector('textarea');
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(field, 'A single solid red square centered on a white background');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    })()`)
    await waitFor("Boolean([...document.querySelectorAll('button')].find(button => button.textContent.includes('Queue image generation') && !button.disabled))")
    await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent.includes('Queue image generation')).click()")
    await waitFor("Boolean(window.__imageRequest) && Boolean([...document.querySelectorAll('article')].find(card => card.textContent.includes('Image generation: A single solid red square'))) ")
    const imageRequest = await evaluate<{ operation: string; request_id: string; input: { prompt: string } }>('window.__imageRequest')
    expect(imageRequest.operation).toBe('image_generate')
    const originalJob = await evaluate<{ id: string }>(`fetch('/api/capabilities/media/jobs').then(response => response.json()).then(value => value.items.find(job => job.operation === 'image_generate' && job.input.prompt === ${JSON.stringify(imageRequest.input.prompt)}))`)
    const duplicate = await evaluate<{ id: string }>(`fetch('/api/capabilities/media/images', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: ${JSON.stringify(JSON.stringify(imageRequest))} }).then(response => response.json())`)
    expect(duplicate.id).toBe(originalJob.id)
    await waitFor(`Boolean([...document.querySelectorAll('article')].find(card => card.textContent.includes(${JSON.stringify(duplicate.id)}) && card.querySelector('[role="status"]')?.textContent.includes('succeeded') && card.querySelector('a[href*="/raw?version="]')))` , 90000)
    const imageJob = await evaluate<{ id: string; status: string; result: { artifact_id: string; version: number } }>(`fetch('/api/capabilities/media/jobs/${duplicate.id}').then(response => response.json())`)
    expect(imageJob.id).toBe(duplicate.id)
    expect(imageJob.status).toBe('succeeded')
    expect(imageJob.result.version).toBeGreaterThan(0)
    const raw = await evaluate<{ status: number; mime: string; size: number; signature: string }>(`fetch('/api/artifacts/${encodeURIComponent(imageJob.result.artifact_id)}/raw?version=${imageJob.result.version}').then(async response => { const bytes = new Uint8Array(await response.arrayBuffer()); return { status: response.status, mime: response.headers.get('content-type'), size: bytes.length, signature: [...bytes.slice(0, 8)].map(value => value.toString(16).padStart(2, '0')).join('') } })`)
    expect(raw.status).toBe(200)
    expect(raw.mime).toContain('image/png')
    expect(raw.size).toBeGreaterThan(100)
    expect(raw.signature).toBe('89504e470d0a1a0a')
    const imageDocumentId = await evaluate<string>('window.__mediaDocumentId')
    await send('Page.reload')
    await waitFor(`window.__mediaDocumentId && window.__mediaDocumentId !== ${JSON.stringify(imageDocumentId)} && Boolean([...document.querySelectorAll('article')].find(card => card.textContent.includes(${JSON.stringify(duplicate.id)}) && card.querySelector('[role="status"]')?.textContent.includes('succeeded')))`)
    await evaluate("document.getElementById('open-videos').click()")
    await waitFor("Boolean(document.querySelector('[aria-label=\"Video generation\"]'))")
    const videoCaps = await evaluate<{ available: boolean }>("fetch('/api/capabilities/media/videos').then(response => response.json())")
    expect(videoCaps.available).toBe(false)
    await waitFor("document.body.textContent.includes('Unavailable')")
    expect(await evaluate<string>('document.body.textContent')).toContain('Unavailable')
    expect(await evaluate<boolean>("[...document.querySelectorAll('button')].some(button => button.textContent.includes('Queue video') && button.disabled)")).toBe(true)
    expect(await evaluate<string>('location.hash')).toBe('')
    await evaluate("document.getElementById('open-sketches').click()")
    await waitFor(`Boolean(document.querySelector('[aria-label="Drawing canvas"]')) && document.body.textContent.includes(${JSON.stringify(seeded.sketch_id)})`)
    await evaluate(`(() => {
      const original = window.fetch.bind(window);
      window.__heldMediaRequest = null;
      window.__heldMediaStatus = null;
      window.__releaseMediaSubmit = null;
      window.fetch = (input, init) => {
        if (String(input) === '/api/capabilities/media/jobs' && init?.method === 'POST') {
          window.fetch = original;
          window.__heldMediaRequest = JSON.parse(init.body);
          return new Promise(resolve => {
            window.__releaseMediaSubmit = () => original(input, init).then(response => {
              window.__heldMediaStatus = response.status;
              resolve(response);
            });
          });
        }
        return original(input, init);
      };
    })()`)
    await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent.includes('Queue PNG export')).click()")
    await waitFor("Boolean(window.__releaseMediaSubmit)")
    const held = await evaluate<{ operation: string; request_id: string }>('window.__heldMediaRequest')
    expect(held.operation).toBe('sketch_export')
    await evaluate("document.getElementById('open-videos').click()")
    await waitFor("Boolean(document.querySelector('[aria-label=\"Video generation\"]'))")
    const routeBeforeRelease = await evaluate<string>('location.pathname + location.search + location.hash')
    const navigationsBeforeRelease = await evaluate<number>('window.__mediaNavigations')
    await evaluate('window.__releaseMediaSubmit()')
    await waitFor("window.__heldMediaStatus === 202")
    await waitFor(`fetch('/api/capabilities/media/jobs').then(response => response.json()).then(value => value.items.some(job => job.operation === 'sketch_export' && job.sketch_id === ${JSON.stringify(seeded.sketch_id)} && job.id !== ${JSON.stringify(seeded.completed_job_id)} && job.status === 'succeeded'))`)
    expect(await evaluate<number>('window.__mediaNavigations')).toBe(navigationsBeforeRelease)
    expect(await evaluate<string>('location.pathname + location.search + location.hash')).toBe(routeBeforeRelease)
    expect(await evaluate<boolean>("Boolean(document.querySelector('[aria-label=\"Video generation\"]'))")).toBe(true)
  } finally {
    socket?.close()
    browser?.kill('SIGTERM')
    await vite?.close()
    api.kill('SIGTERM')
    if (directory) await rm(directory, { recursive: true, force: true, maxRetries: 12, retryDelay: 100 })
  }
}, 120000)
