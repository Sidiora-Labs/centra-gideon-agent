import { spawn } from 'node:child_process'
import { createServer as createNetServer } from 'node:net'
import { join, resolve } from 'node:path'
import { createServer as createViteServer, transformWithEsbuild } from 'vite'
import { expect, it } from 'vitest'
import { startBrowserHarness } from '../../../test-support/browserHarness'

async function freePort(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(done => server.close(() => done()))
  return port
}

it('cleans a selected native image into a durable versioned asset and restores its Studio route', async () => {
  const root = resolve(process.cwd(), '../..')
  const port = await freePort()
  const origin = `http://127.0.0.1:${port}`
  const api = spawn(process.env.GIDEON_TEST_PYTHON || 'python3',
    [join(root, 'apps/assistant/test-support/studio_server.py'), origin],
    { env: { PATH: process.env.PATH || '', PYTHONPATH: join(root, 'runtime') } })
  let vite: Awaited<ReturnType<typeof createViteServer>> | undefined
  let browser: Awaited<ReturnType<typeof startBrowserHarness>> | undefined
  try {
    const seeded = await new Promise<{ api_port: number; artifact_id: string; version: number }>((done, fail) => {
      let stdout = '', stderr = ''
      const timer = setTimeout(() => fail(new Error(`Media API start timed out: ${stderr}`)), 20000)
      api.stdout.on('data', chunk => {
        stdout += String(chunk)
        if (stdout.includes('\n')) { clearTimeout(timer); done(JSON.parse(stdout.split('\n')[0])) }
      })
      api.stderr.on('data', chunk => { stderr += String(chunk) })
      api.once('exit', code => { clearTimeout(timer); fail(new Error(`Media API exited ${code}: ${stderr}`)) })
    })
    const entry = `
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import { ownerScope } from '/src/shared/auth.web';
      import { parseShellRoute, serializeShellRoute, createShellRoute } from '/src/shared/shell/shellRoutes';
      import { createStudioRoute } from '/src/features/studio/studioRoutes';
      import StudioWorkspace from '/src/features/studio/StudioWorkspace.web';
      window.__derivedDocument = crypto.randomUUID();
      const back = { destination: 'chat', sessionId: 'cleanup-conversation', selectionId: 'cleanup-message', scrollY: 90 };
      const initial = parseShellRoute(location.href, location.origin);
      function App() {
        const [route, setRoute] = React.useState(() => initial.kind === 'route' ? initial : createStudioRoute('capabilities/media/cleanup', back));
        const [open, setOpen] = React.useState(initial.kind === 'route' && initial.view === 'workspace');
        const scope = ownerScope(location.origin, { user: 'studio-owner' });
        const navigate = next => { history.pushState(null, '', serializeShellRoute(next)); setRoute(next); setOpen(true); };
        return <><button id="open-cleanup" onClick={() => navigate(createStudioRoute('capabilities/media/cleanup', back))}>Open cleanup</button>
          {open && <div style={{height:'calc(100vh - 44px)'}}><StudioWorkspace route={route} scope={scope} navigate={navigate}
            returnTo={route.returnTo} onReturn={() => { document.body.dataset.returned = JSON.stringify(route.returnTo); history.pushState(null, '', serializeShellRoute(createShellRoute('chat', { sessionId: route.returnTo?.sessionId }))); setOpen(false); }} /></div>}</>;
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
      plugins: [{ name: 'derived-media-browser-entry',
        resolveId(id) { if (id === '/derived-media-browser.tsx') return '\0derived-media-browser' },
        async load(id) { if (id === '\0derived-media-browser') return (await transformWithEsbuild(entry, 'derived-media-browser.tsx', { loader: 'tsx', jsx: 'automatic' })).code },
        configureServer(server) { server.middlewares.use('/assistant', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/derived-media-browser.tsx"></script></body></html>')
        }) },
      }], server: { host: '127.0.0.1', port, strictPort: true, fs: { allow: [root] },
        proxy: { '/api': `http://127.0.0.1:${seeded.api_port}` } },
    })
    await vite.listen()
    browser = await startBrowserHarness()
    await browser.navigate(`${origin}/assistant`)
    await browser.waitFor("Boolean(document.getElementById('open-cleanup'))")
    expect(await browser.evaluate<number>("fetch('/api/capabilities/media/library?kind=image').then(response => response.status)")).toBe(403)
    expect(await browser.evaluate<number>("fetch('/api/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'studio-owner',password:'correct-horse-battery-staple',totp:''})}).then(response => response.status)")).toBe(200)
    await browser.evaluate("document.getElementById('open-cleanup').click()")
    await browser.waitFor("Boolean([...document.querySelectorAll('select[aria-label=\"Source image\"] option')].find(option => option.textContent.includes('Studio harbor image')))")
    expect(await browser.evaluate<boolean>("Boolean(document.querySelector('input[aria-label=\"Source image artifact\"]'))")).toBe(false)
    await browser.evaluate(`(() => { const select = document.querySelector('select[aria-label="Source image"]'); select.value = [...select.options].find(option => option.textContent.includes('Studio harbor image')).value; select.dispatchEvent(new Event('change', { bubbles: true })); })()`)
    await browser.waitFor("document.body.textContent.includes('Pinned source: Studio harbor image · version 1')")
    await browser.evaluate("[...document.querySelectorAll('button')].find(button => button.textContent === 'Add transform').click()")
    await browser.waitFor("document.body.textContent.includes('1. resize') && ![...document.querySelectorAll('button')].find(button => button.textContent === 'Queue cleanup').disabled")
    await browser.evaluate(`(() => { const original = window.fetch.bind(window); window.__cleanupRequest = null; window.fetch = (input, options) => { if (String(input) === '/api/capabilities/media/jobs' && options?.method === 'POST') window.__cleanupRequest = JSON.parse(options.body); return original(input, options); }; })()`)
    await browser.evaluate("[...document.querySelectorAll('button')].find(button => button.textContent === 'Queue cleanup').click()")
    await browser.waitFor("Boolean(window.__cleanupRequest) && Boolean([...document.querySelectorAll('article')].find(card => card.textContent.includes('Image cleanup') && card.querySelector('[role=\"status\"]')?.textContent.includes('succeeded')))", 'native cleanup job succeeded', 30000)
    const request = await browser.evaluate<{ operation: string; input: { source_artifact_id: string; source_version: number; operations: { op: string; width: number; height: number }[] } }>('window.__cleanupRequest')
    expect(request.operation).toBe('image_cleanup')
    expect(request.input).toEqual({ source_artifact_id: seeded.artifact_id, source_version: seeded.version, operations: [{ op: 'resize', width: 512, height: 512 }] })
    const job = await browser.evaluate<{ id: string; status: string; result: { artifact_id: string; version: number } }>(`fetch('/api/capabilities/media/jobs').then(response => response.json()).then(value => value.items.find(item => item.operation === 'image_cleanup' && item.input.source_artifact_id === ${JSON.stringify(seeded.artifact_id)}))`)
    expect(job.status).toBe('succeeded')
    expect(job.result.artifact_id).not.toBe(seeded.artifact_id)
    expect(job.result.version).toBeGreaterThan(0)
    const rawUrl = `/api/artifacts/${encodeURIComponent(job.result.artifact_id)}/raw?version=${job.result.version}`
    const raw = await browser.evaluate<{ status: number; mime: string; signature: string; width: number; height: number }>(`fetch(${JSON.stringify(rawUrl)}).then(async response => { const bytes = new Uint8Array(await response.arrayBuffer()); const data = new DataView(bytes.buffer); return { status: response.status, mime: response.headers.get('content-type'), signature: [...bytes.slice(0, 8)].map(value => value.toString(16).padStart(2, '0')).join(''), width: data.getUint32(16), height: data.getUint32(20) } })`)
    expect(raw).toMatchObject({ status: 200, signature: '89504e470d0a1a0a', width: 512, height: 512 })
    expect(raw.mime).toContain('image/png')
    expect(await browser.evaluate<string>('location.hash')).toBe('')
    await browser.evaluate("document.querySelector('a[href=\"#/capabilities/media?view=library\"]').click()")
    await browser.waitFor("Boolean([...document.querySelectorAll('article button')].find(button => button.textContent === 'Image cleanup'))", 'completed cleanup in native library')
    await browser.evaluate("[...document.querySelectorAll('article button')].find(button => button.textContent === 'Image cleanup').click()")
    await browser.waitFor("Boolean(document.querySelector('[aria-label=\"Media details\"]'))")
    expect(await browser.evaluate<string>('location.hash')).toBe('')
    const documentId = await browser.evaluate<string>('window.__derivedDocument')
    await browser.command('Page.reload')
    await browser.waitFor(`window.__derivedDocument && window.__derivedDocument !== ${JSON.stringify(documentId)} && document.querySelector('[aria-label="Media details"]')?.textContent.includes('Image cleanup')`, 'new document restores completed asset')
    expect(await browser.evaluate<string>('location.hash')).toBe('')
    await browser.evaluate("document.querySelector('.gideon-workspace-back').click()")
    await browser.waitFor("Boolean(document.body.dataset.returned)")
    expect(await browser.evaluate<string>('document.body.dataset.returned')).toContain('"selectionId":"cleanup-message","scrollY":90')
    expect(await browser.evaluate<string>('location.pathname')).toBe('/assistant/chat')
    expect(await browser.evaluate<string>('location.search')).toContain('cleanup-conversation')
    expect(await browser.evaluate<string>('location.hash')).toBe('')
  } finally {
    await browser?.close()
    await vite?.close()
    api.kill('SIGTERM')
  }
}, 90000)
