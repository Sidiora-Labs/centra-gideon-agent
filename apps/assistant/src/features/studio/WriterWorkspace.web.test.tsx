import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer as createViteServer } from 'vite'
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

it('creates a canonical work, saves and previews a pinned manuscript, exports real files, and recovers a conflicted draft in Chromium', async () => {
  const repository = resolve(process.cwd(), '../..')
  const assistant = join(repository, 'apps/assistant')
  const modules = join(assistant, 'node_modules')
  const home = await mkdtemp(join(tmpdir(), 'gideon-writer-native-'))
  const entryDirectory = await mkdtemp(join(assistant, '.writer-browser-'))
  const entryFile = join(entryDirectory, 'entry.tsx')
  const python = process.env.GIDEON_TEST_PYTHON || 'python3'
  const port = await freePort()
  const origin = `http://127.0.0.1:${port}`
  const nativeProgram = `
import asyncio, json, sys
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.handlers import auth, capabilities_creative
from gideon.security.auth import credentials
from gideon.workspace.capabilities.creative import IngredientStore
home, origin = Path(sys.argv[1]), sys.argv[2]
(home / 'config.json').write_text(json.dumps({'auth': {'login_enabled': True}}))
credentials.set_password('writer-test-owner', 'correct-horse-battery-staple')
token_auth.use_ephemeral_secret()
async def main():
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=10000)])
    app['port'] = 10000
    app['allowed_origins'] = {origin}
    app[capabilities_creative.STORE] = IngredientStore(home)
    app.router.add_post('/api/auth/login', auth.api_auth_login)
    app.router.add_get('/api/auth/session', auth.api_auth_session)
    capabilities_creative.register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    print(site._server.sockets[0].getsockname()[1], flush=True)
    try: await asyncio.Event().wait()
    finally: await runner.cleanup()
asyncio.run(main())
`
  const native = spawn(python, ['-c', nativeProgram, home, origin],
    { cwd: repository, env: { ...process.env, PYTHONPATH: join(repository, 'runtime'), GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'] })
  let vite: Awaited<ReturnType<typeof createViteServer>> | undefined
  let browser: Awaited<ReturnType<typeof startBrowserHarness>> | undefined
  try {
    const apiPort = await new Promise<number>((done, fail) => {
      let output = '', errors = ''
      const timeout = setTimeout(() => fail(new Error(`Writer server timeout: ${errors}`)), 20000)
      native.stdout?.on('data', chunk => {
        output += String(chunk)
        const line = output.split('\n')[0]
        if (/^\d+$/.test(line)) { clearTimeout(timeout); done(Number(line)) }
      })
      native.stderr?.on('data', chunk => { errors += String(chunk) })
      native.once('exit', code => { clearTimeout(timeout); fail(new Error(`Writer server exited ${code}: ${errors}`)) })
    })
    const entry = `
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import { ownerScope } from '/src/shared/auth.web';
      import { createStudioRoute } from '/src/features/studio/studioRoutes';
      import WriterWorkspace from '/src/features/studio/WriterWorkspace.web';
      const scope = ownerScope(location.origin, { user: 'writer-test-owner' });
      window.writerDraftStorageKey = 'gideon-writer:' + scope.cacheKey;
      function App() {
        const [route, setRoute] = React.useState(createStudioRoute('capabilities/creative/works'));
        return React.createElement(WriterWorkspace, { route, scope, navigate: setRoute, onReturn: () => {} });
      }
      createRoot(document.getElementById('root')).render(React.createElement(App));
      window.writerFill = (label, value) => {
        const field = [...document.querySelectorAll('input,textarea')].find(item => item.getAttribute('aria-label') === label)
          || [...document.querySelectorAll('label')].find(item => item.textContent.trim().startsWith(label))?.querySelector('input,textarea');
        if (!field) throw new Error('Missing field ' + label);
        Object.getOwnPropertyDescriptor(Object.getPrototypeOf(field), 'value').set.call(field, value);
        field.dispatchEvent(new Event('input', { bubbles: true }));
        field.dispatchEvent(new Event('change', { bubbles: true }));
      };
      window.writerChoose = (label, value) => {
        const field = [...document.querySelectorAll('label')].find(item => item.textContent.trim().startsWith(label))?.querySelector('select');
        if (!field) throw new Error('Missing selection ' + label);
        Object.getOwnPropertyDescriptor(Object.getPrototypeOf(field), 'value').set.call(field, value);
        field.dispatchEvent(new Event('change', { bubbles: true }));
      };
      window.writerClick = label => {
        const button = [...document.querySelectorAll('button')].find(item => item.textContent.trim() === label);
        if (!button) throw new Error('Missing button ' + label);
        button.click();
      };
    `
    await writeFile(entryFile, entry)
    vite = await createViteServer({ configFile: false, root: assistant,
      resolve: { alias: [
        { find: 'react', replacement: join(modules, 'react') },
        { find: 'react-dom', replacement: join(modules, 'react-dom') },
        { find: 'lucide-react', replacement: join(modules, 'lucide-react') },
        { find: 'framer-motion', replacement: join(modules, 'framer-motion') },
        { find: '@base-ui/react', replacement: join(modules, '@base-ui/react') },
        { find: /^react-native$/, replacement: 'react-native-web' },
      ], dedupe: ['react', 'react-dom'], extensions: ['.web.tsx', '.web.ts', '.tsx', '.ts', '.jsx', '.js'] },
      esbuild: { jsx: 'automatic' },
      optimizeDeps: { include: ['react', 'react-dom/client', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'framer-motion', 'lucide-react'] },
      plugins: [{ name: 'writer-browser-entry', configureServer(server) {
          server.middlewares.use('/assistant', (_request, response) => {
            response.setHeader('Content-Type', 'text/html; charset=utf-8')
            response.end(`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@fs${entryFile}"></script></body></html>`)
          })
        } }],
      server: { host: '127.0.0.1', port, strictPort: true, fs: { allow: [repository] },
        proxy: { '/api': `http://127.0.0.1:${apiPort}` } },
    })
    await vite.listen()
    browser = await startBrowserHarness()
    await browser.navigate(`${origin}/assistant`)
    const login = await browser.evaluate<number>("fetch('/api/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'writer-test-owner',password:'correct-horse-battery-staple',totp:''})}).then(response => response.status)")
    expect(login).toBe(200)
    await browser.waitFor("document.body.textContent.includes('Writing works and exercises')", 'native writer mounted')
    await browser.evaluate("if ([...document.querySelectorAll('button')].some(item => item.textContent.trim() === 'Retry')) writerClick('Retry')")
    await browser.waitFor("document.body.textContent.includes('No writing works found.')", 'empty native work catalog')
    await browser.evaluate("writerFill('Work title', 'Writer browser work')")
    await browser.evaluate("writerClick('Save work details')")
    await browser.waitFor("document.body.textContent.includes('Work revision 1')", 'saved work')
    await browser.evaluate("writerFill('Manuscript', 'The last train crossed the city.')")
    await browser.waitFor("[...document.querySelectorAll('button')].find(item => item.textContent.trim() === 'Save new draft' && !item.disabled)", 'draft save enabled')
    await browser.evaluate("writerClick('Save new draft')")
    await browser.waitFor("document.body.textContent.includes('Work revision 2')", 'saved manuscript')
    await browser.evaluate("writerClick('Preview selected')")
    await browser.waitFor("document.querySelector('[aria-label=\"Manuscript preview\"]')?.textContent.includes('The last train crossed the city.')", 'rendered selected draft')
    await browser.evaluate("writerClick('Export selected')")
    await browser.waitFor("document.body.textContent.includes('Prepare export')", 'pinned export form')
    await browser.evaluate("writerFill('Export title', 'Writer browser book')")
    await browser.evaluate("writerFill('Export creator', 'Writer Owner')")
    await browser.evaluate("writerFill('Export identifier', 'urn:uuid:writer-browser-book')")
    await browser.waitFor("[...document.querySelectorAll('button')].find(item => item.textContent.trim() === 'Create EPUB and print PDF' && !item.disabled)", 'export creation enabled')
    await browser.evaluate("writerClick('Create EPUB and print PDF')")
    await browser.waitFor("document.querySelector('[aria-label=\"Manuscript export Writer browser book\"]')", 'rendered export receipt')
    const downloads = await browser.evaluate<Array<{ status: number; mime: string }>>(`Promise.all([...document.querySelectorAll('[aria-label="Manuscript export Writer browser book"] a')].map(async link => {
      const response = await fetch(link.href); return { status: response.status, mime: response.headers.get('content-type') || '' };
    }))`)
    expect(downloads).toHaveLength(2)
    expect(downloads.map(item => item.status)).toEqual([200, 200])
    expect(downloads.map(item => item.mime).join(' ')).toContain('application/epub+zip')
    expect(downloads.map(item => item.mime).join(' ')).toContain('application/pdf')
    expect(await browser.evaluate<string>('location.hash')).toBe('')
    await browser.evaluate("writerClick('Writing')")
    await browser.waitFor("document.body.textContent.includes('Work revision 2')", 'work after export')
    await browser.evaluate("writerFill('Manuscript', 'A recoverable unsaved ending.')")
    const manuscriptEditor = "[...document.querySelectorAll('textarea')].find(item => item.closest('label')?.querySelector('span.sr-only')?.textContent.trim() === 'Manuscript')"
    await browser.waitFor(`${manuscriptEditor}?.value === 'A recoverable unsaved ending.'`, 'local draft edit')
    const work = await browser.evaluate<{ id: string; revision: number }>("fetch('/api/capabilities/creative/works').then(response => response.json()).then(data => data.items[0])")
    expect(await browser.evaluate<string>(`localStorage.getItem(window.writerDraftStorageKey + ':' + ${JSON.stringify(work.id)}) || ''`)).toBe('A recoverable unsaved ending.')
    const changed = await browser.evaluate<number>(`fetch('/api/capabilities/creative/works/${encodeURIComponent(work.id)}', {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:${work.revision},prompt:'Externally revised'})}).then(response => response.status)`)
    expect(changed).toBe(200)
    await browser.evaluate("writerClick('Save new draft')")
    await browser.waitFor("document.body.textContent.includes('Work changed; reload before saving a draft')", 'revision conflict')
    await browser.evaluate("writerClick('Retry')")
    await browser.waitFor("document.body.textContent.includes('Work revision 3')", 'reloaded canonical revision')
    expect(await browser.evaluate<string>(`${manuscriptEditor}?.value || ''`)).toBe('A recoverable unsaved ending.')
    await browser.evaluate("writerClick('Save new draft')")
    await browser.waitFor("document.body.textContent.includes('Work revision 4')", 'recovered manuscript saved')
    await browser.evaluate("writerClick('Universes')")
    await browser.waitFor("document.body.textContent.includes('No universes found.')", 'universe catalog')
    await browser.evaluate("writerFill('Universe title', 'Writer browser universe')")
    await browser.evaluate("writerClick('Save universe')")
    await browser.waitFor("document.body.textContent.includes('Universe revision 1')", 'saved universe')
    const universe = await browser.evaluate<{ id: string; revision: number }>("fetch('/api/capabilities/creative/universes').then(response => response.json()).then(data => data.items[0])")
    await browser.evaluate("writerClick('Writing')")
    await browser.waitFor("document.body.textContent.includes('Work revision 4')", 'work before linking universe')
    await browser.waitFor(`!![...document.querySelectorAll('label')].find(item => item.textContent.trim().startsWith('Pin universe'))?.querySelector('option[value="${universe.id}"]')`, 'available universe')
    await browser.evaluate(`writerChoose('Pin universe', ${JSON.stringify(universe.id)})`)
    await browser.waitFor("document.body.textContent.includes('Universe pinned revision 1')", 'selected universe revision')
    await browser.evaluate("writerClick('Save work details')")
    await browser.waitFor("document.body.textContent.includes('Work revision 5')", 'work linked to universe')
    const linkedWork = await browser.evaluate<{ universe_ref: { id: string; revision: number } | null }>(`fetch('/api/capabilities/creative/works/${encodeURIComponent(work.id)}').then(response => response.json())`)
    expect(linkedWork.universe_ref).toEqual({ id: universe.id, revision: universe.revision })
    await browser.evaluate("writerClick('Read pinned context')")
    await browser.waitFor("[...document.querySelectorAll('textarea')].some(item => item.value.includes('Writer browser universe'))", 'rendered pinned universe context')
  } finally {
    await browser?.close()
    await vite?.close()
    if (native.exitCode === null) await new Promise<void>(done => { native.once('exit', () => done()); native.kill('SIGTERM') })
    await rm(home, { recursive: true, force: true })
    await rm(entryDirectory, { recursive: true, force: true })
  }
}, 120000)
