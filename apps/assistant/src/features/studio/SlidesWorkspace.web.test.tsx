import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer as createViteServer, transformWithEsbuild } from 'vite'
import { describe, expect, it } from 'vitest'
import { packSlides, slidesFromOutline, unpackSlides } from './SlidesWorkspace.web'

describe('Slides model identity', () => {
  it('keeps slide IDs and notes through reorder and a native-model round trip', () => {
    const entries = slidesFromOutline('## Finding\n- Evidence\n<!-- notes: Source A -->\n## Decision\n- Proceed\n<!-- notes: Owner review -->')
    expect(entries).toHaveLength(2)
    expect(entries[0].id).not.toBe(entries[1].id)
    const model = packSlides({ title: 'Review', width_in: 0, height_in: 0, slides: [] }, [entries[1], entries[0]])
    const reloaded = unpackSlides(model)
    expect(reloaded.map(entry => entry.id)).toEqual([entries[1].id, entries[0].id])
    expect(reloaded.map(entry => entry.slide.notes)).toEqual(['Owner review', 'Source A'])
    expect(reloaded.map(entry => entry.slide.bullets[0].text)).toEqual(['Proceed', 'Evidence'])
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

const nativeServer = String.raw`
import asyncio, json, os, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
from aiohttp import web

async def main(origin):
    with tempfile.TemporaryDirectory(prefix='gideon-slides-test-') as directory:
        home = Path(directory)
        os.environ['GIDEON_HOME'] = str(home)
        from gideon.interfaces.dashboard import token_auth
        from gideon.security.auth import credentials
        from gideon.interfaces.dashboard.handlers import auth
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
        app['state'] = SimpleNamespace(_restricted_keys=set(), _sessions={})
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

it('creates, edits, reorders, reloads, previews and downloads an owner-scoped PPTX in Chromium', async () => {
  const root = resolve(process.cwd(), '../..')
  const port = await freePort()
  const origin = `http://127.0.0.1:${port}`
  const api = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', nativeServer, origin],
    { env: { ...process.env, PYTHONPATH: join(root, 'runtime') } })
  let vite: Awaited<ReturnType<typeof createViteServer>> | undefined
  let browser: ChildProcessWithoutNullStreams | undefined
  let socket: WebSocket | undefined
  let directory: string | undefined
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
      import SlidesWorkspace from '/src/features/studio/SlidesWorkspace.web';
      function App() {
        const [owner, setOwner] = React.useState('slides-owner');
        const [artifactId, setArtifactId] = React.useState('');
        const scope = React.useMemo(() => ownerScope(location.origin, { user: owner }), [owner]);
        return <><button id="other-owner" onClick={() => setOwner('other-owner')}>Other owner</button>
          <button id="other-record" onClick={() => setArtifactId('second-review')}>Other record</button>
          <SlidesWorkspace scope={scope} artifactId={artifactId} /></>;
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
      plugins: [{ name: 'slides-browser-entry',
        resolveId(id) { if (id === '/slides-browser.tsx') return '\0slides-browser' },
        async load(id) { if (id === '\0slides-browser') return (await transformWithEsbuild(entry, 'slides-browser.tsx', { loader: 'tsx', jsx: 'automatic' })).code },
        configureServer(server) { server.middlewares.use('/assistant', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><html><body><div id="root"></div><script type="module" src="/slides-browser.tsx"></script></body></html>')
        }) },
      }],
      server: { host: '127.0.0.1', port, strictPort: true, fs: { allow: [root] },
        proxy: { '/api': `http://127.0.0.1:${started.api_port}` } },
    })
    await vite.listen()
    directory = await mkdtemp(join(tmpdir(), 'gideon-slides-browser-'))
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
      if (!response.result?.result) throw new Error(`Browser evaluation did not complete: ${JSON.stringify(response)}`)
      return response.result.result.value as T
    }
    const waitFor = async (expression: string) => {
      for (let attempt = 0; attempt < 150; attempt++) {
        if (await evaluate<boolean>(expression)) return
        await new Promise(done => setTimeout(done, 100))
      }
      throw new Error(`Browser condition timed out: ${expression}; ${await evaluate<string>('document.body.textContent')}`)
    }
    const setValue = (selector: string, value: string) => evaluate(`(() => {
      const node = document.querySelector(${JSON.stringify(selector)});
      const setter = Object.getOwnPropertyDescriptor(node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value').set;
      setter.call(node, ${JSON.stringify(value)}); node.dispatchEvent(new Event('input', {bubbles:true}));
    })()`)
    const click = (label: string) => evaluate(`(() => { const node = [...document.querySelectorAll('button')].find(button => button.textContent.trim() === ${JSON.stringify(label)}); if (!node) throw new Error('Missing button: ' + ${JSON.stringify(label)}); node.click(); })()`)
    const holdNativeSaveResponse = () => evaluate(`(() => {
      const original = window.fetch;
      window.__slidesRelease = undefined;
      window.fetch = (input, options) => {
        const url = typeof input === 'string' ? input : input.url;
        if ((options?.method || input?.method) !== 'PUT' || !new URL(url, location.origin).pathname.endsWith('/model')) return original(input, options);
        window.fetch = original;
        return original(input, options).then(response => new Promise(resolve => {
          window.__slidesHeldStatus = response.status;
          window.__slidesRelease = () => resolve(response);
        }));
      };
    })()`)

    await send('Page.navigate', { url: `${origin}/assistant` })
    await waitFor("Boolean(document.querySelector('[aria-label=\"New presentation name\"]'))")
    expect(await evaluate<number>("fetch('/api/artifacts?kind=pptx').then(response => response.status)")).toBe(403)
    expect(await evaluate<number>(`fetch('/api/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'slides-owner',password:'correct-horse-battery-staple',totp:''})}).then(response => response.status)`)).toBe(200)
    await send('Page.reload')
    await waitFor("document.body.textContent.includes('Your presentation will be saved to this account')")
    await setValue('[aria-label="New presentation name"]', 'Quarterly review')
    await setValue('[aria-label="New presentation outline"]', '## First finding\n- Source result\n<!-- notes: First notes -->\n## Decision\n- Proceed\n<!-- notes: Decision notes -->')
    await click('Create presentation (2 slides)')
    await waitFor("document.body.textContent.includes('saved version 1')")
    await click('Move down')
    await waitFor("document.querySelector('[aria-label=\"Slides\"] button').textContent.includes('Decision')")
    await setValue('[aria-label="Speaker notes"]', 'Updated first notes')
    await click('Save and export PPTX')
    await waitFor("document.body.textContent.includes('Download PPTX version 2')")
    const href = await evaluate<string>("document.querySelector('a[download]').getAttribute('href')")
    expect(href).toMatch(/^\/api\/artifacts\/[^/]+\/raw\?version=2$/)
    expect(await evaluate<boolean>(`fetch(${JSON.stringify(href)}).then(async response => response.ok && (await response.arrayBuffer()).byteLength > 1000)`)).toBe(true)
    await click('Render preview of version 2')
    await waitFor("document.body.textContent.includes('Preview of') || document.body.textContent.includes('Slide preview failed:')")
    expect(await evaluate<string>("document.body.textContent")).toContain('Preview of')
    await click('Reload')
    await waitFor("document.body.textContent.includes('saved version 2')")
    expect(await evaluate<string>("document.querySelector('[aria-label=\"Slides\"] button').textContent")).toContain('Decision')
    expect(await evaluate<string>("document.querySelector('[aria-label=\"Speaker notes\"]').value")).toBe('Updated first notes')

    expect(await evaluate<number>(`(async () => {
      const source = await (await fetch('/api/artifacts/quarterly-review/model')).json();
      const model = { ...source.model, title: 'Second review', slides: [{ ...source.model.slides[0], title: 'Secondary decision' }] };
      return (await fetch('/api/artifacts/deck', { method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ name:'Second review', slug:'second-review', model }) })).status;
    })()`)).toBe(201)
    await setValue('[aria-label="Speaker notes"]', 'Pending old record')
    await holdNativeSaveResponse()
    await click('Save and export PPTX')
    await waitFor("window.__slidesHeldStatus === 200 && typeof window.__slidesRelease === 'function'")
    await evaluate("document.getElementById('other-record').click()")
    expect(await evaluate<boolean>("document.body.textContent.includes('Pending old record') || Boolean(document.querySelector('a[download]'))")).toBe(false)
    await waitFor("document.body.textContent.includes('Secondary decision') && document.body.textContent.includes('saved version 1')")
    await evaluate("window.__slidesRelease()")
    await new Promise(done => setTimeout(done, 200))
    expect(await evaluate<boolean>("document.body.textContent.includes('Pending old record') || document.body.textContent.includes('Download PPTX version 3') || Boolean(document.querySelector('[role=alert]'))")).toBe(false)
    expect(await evaluate<string>("document.querySelector('[aria-label=\"Slide title\"]').value")).toBe('Secondary decision')

    await setValue('[aria-label="Speaker notes"]', 'Pending old owner')
    await holdNativeSaveResponse()
    await click('Save and export PPTX')
    await waitFor("window.__slidesHeldStatus === 200 && typeof window.__slidesRelease === 'function'")
    await evaluate("document.getElementById('other-owner').click()")
    expect(await evaluate<boolean>("document.body.textContent.includes('Pending old owner') || Boolean(document.querySelector('a[download]'))")).toBe(false)
    await waitFor("document.body.textContent.includes('signed-in Studio account changed')")
    await evaluate("window.__slidesRelease()")
    await new Promise(done => setTimeout(done, 200))
    expect(await evaluate<boolean>("document.body.textContent.includes('Pending old owner') || document.body.textContent.includes('saved version 2') || Boolean(document.querySelector('a[download]'))")).toBe(false)
  } finally {
    socket?.close()
    browser?.kill('SIGTERM')
    await vite?.close()
    api.kill('SIGTERM')
    if (directory) await rm(directory, { recursive: true, force: true })
  }
}, 60000)
