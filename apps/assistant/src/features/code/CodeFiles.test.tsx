import * as React from 'react'
import { execFile, spawn, type ChildProcess } from 'node:child_process'
import { createServer } from 'node:http'
import { promisify } from 'node:util'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { buildSync } from 'esbuild'
import { resolve } from 'node:path'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { GatewayError } from '../../shared/transport.web'
import CodeFiles, { listProjectFiles, moveProjectDraft, readProjectFile, renameProjectFile, saveProjectFile } from './CodeFiles.web'

const server = String.raw`
import asyncio, json, os, tempfile
from pathlib import Path
from aiohttp import web

async def main():
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary) / 'home'
        workspace = Path(temporary) / 'workspace'
        home.mkdir()
        workspace.mkdir()
        os.environ['GIDEON_HOME'] = str(home)
        os.environ['GIDEON_WORKSPACE'] = str(workspace)
        from gideon.interfaces.dashboard.handlers import api_file_list, api_file_read, api_file_write, api_file_move

        project = workspace / 'project'
        project.mkdir()
        second = workspace / 'second'
        second.mkdir()
        (second / 'other.txt').write_text('other owner', encoding='utf-8')
        (project / 'notes.txt').write_text('first', encoding='utf-8')
        (project / 'binary.bin').write_bytes(b'\x00\x01')
        app = web.Application()
        async def delayed_file_list(request):
            if request.query.get('path') == str(project):
                await asyncio.sleep(0.4)
            return await api_file_list(request)
        app.router.add_get('/api/file-list', delayed_file_list)
        app.router.add_get('/api/file-read', api_file_read)
        app.router.add_post('/api/file-write', api_file_write)
        async def delayed_file_move(request):
            await asyncio.sleep(0.4)
            return await api_file_move(request)
        app.router.add_post('/api/file-move', delayed_file_move)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        print(json.dumps({'origin': f'http://127.0.0.1:{port}', 'root': str(project), 'second': str(second)}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

asyncio.run(main())
`

describe('Code files against native dashboard file handlers', () => {
  let backend: ChildProcess
  let origin = ''
  let root = ''
  let second = ''
  const originalFetch = globalThis.fetch

  beforeAll(async () => {
    const repository = resolve(process.cwd(), '../..')
    backend = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-c', server], {
      env: { ...process.env, PYTHONPATH: resolve(repository, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'],
    })
    const ready = await new Promise<{ origin: string; root: string; second: string }>((accept, reject) => {
      let output = '', errors = ''
      backend.stderr!.on('data', chunk => { errors = (errors + String(chunk)).slice(-4000) })
      backend.on('exit', code => reject(new Error(`Native file server exited ${code}: ${errors}`)))
      backend.stdout!.on('data', chunk => {
        output += String(chunk)
        const line = output.split('\n').find(value => value.startsWith('{"origin"'))
        if (line) accept(JSON.parse(line))
      })
    })
    origin = ready.origin; root = ready.root; second = ready.second
    globalThis.fetch = (input, init) => originalFetch(new URL(String(input), origin), init)
  }, 30000)

  afterAll(() => { globalThis.fetch = originalFetch; backend?.kill() })

  it('lists, edits, reloads, reopens and renames a real project file with server version checks', async () => {
    const path = `${root}/notes.txt`
    expect((await listProjectFiles(root, root)).entries.map(entry => entry.path)).toContain(path)
    const opened = await readProjectFile(path, root)
    expect(opened).toMatchObject({ content: 'first', binary: false, truncated: false })
    expect(opened.validator).toMatch(/^[0-9a-f]{64}$/)
    const nextValidator = await saveProjectFile(path, root, opened.validator, 'edited')
    expect(nextValidator).not.toBe(opened.validator)
    expect((await readProjectFile(path, root)).content).toBe('edited')
    await expect(saveProjectFile(path, root, opened.validator, 'stale'))
      .rejects.toMatchObject({ status: 409 } satisfies Partial<GatewayError>)
    expect((await readProjectFile(path, root)).content).toBe('edited')
    const renamed = await renameProjectFile(path, root, 'renamed.txt')
    expect(renamed).toBe(`${root}/renamed.txt`)
    expect((await readProjectFile(renamed, root)).content).toBe('edited')
    await expect(readProjectFile(path, root)).rejects.toMatchObject({ status: 404 } satisfies Partial<GatewayError>)
  }, 30000)

  it('uses the real binary and server root restrictions', async () => {
    expect((await readProjectFile(`${root}/binary.bin`, root)).binary).toBe(true)
    await expect(readProjectFile('/etc/passwd', root)).rejects.toThrow('outside the selected project')
    await expect(readProjectFile(`${root}/missing.txt`, root)).rejects.toMatchObject({ status: 404 })
  })

  it('moves an unsaved browser draft with the file identity after a native rename', async () => {
    const memory = new Map<string, string>()
    const prior = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
    Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: {
      getItem: (key: string) => memory.get(key) ?? null,
      setItem: (key: string, value: string) => { memory.set(key, value) },
      removeItem: (key: string) => { memory.delete(key) },
    } })
    try {
      const scope = { ownerId: 'owner-1', runtimeOrigin: origin, cacheKey: 'owner-1' }
      const from = `${root}/renamed.txt`
      const to = `${root}/final.txt`
      const oldKey = `gideon:code-draft:${encodeURIComponent(scope.cacheKey)}:project-1:${encodeURIComponent(from)}`
      const nextKey = `gideon:code-draft:${encodeURIComponent(scope.cacheKey)}:project-1:${encodeURIComponent(to)}`
      memory.set(oldKey, JSON.stringify({ path: from, base: 'server', text: 'unsaved', validator: 'v1' }))
      expect(await renameProjectFile(from, root, 'final.txt')).toBe(to)
      moveProjectDraft(scope, 'project-1', from, to)
      expect(memory.has(oldKey)).toBe(false)
      expect(JSON.parse(memory.get(nextKey) || '{}')).toMatchObject({ path: to, text: 'unsaved', validator: 'v1' })
    } finally {
      if (prior) Object.defineProperty(globalThis, 'sessionStorage', prior)
      else delete (globalThis as { sessionStorage?: Storage }).sessionStorage
    }
  })

  it('keeps file controls labelled and responsive in the native module', () => {
    const html = renderToStaticMarkup(<CodeFiles project={{ project: { id: 'project-1', name: 'Project', workspace_dir: root }, detection: { types: [] } }}
      scope={{ ownerId: 'owner-1', runtimeOrigin: origin, cacheKey: 'owner-1' }} />)
    expect(html).toContain('aria-label="Project files"')
    expect(html).toContain('aria-label="Project file browser"')
    expect(html).toContain('minmax(min(100%, 310px), 1fr)')
  })

  it('does not render a delayed old-owner file listing after project and owner switch in Chromium', async () => {
    const source = `
      import * as React from 'react';
      import { createRoot } from 'react-dom/client';
      import CodeFiles from './src/features/code/CodeFiles.web';
      function Harness() {
        const [switched, setSwitched] = React.useState(false);
        React.useEffect(() => { const timer = setTimeout(() => setSwitched(true), 40); return () => clearTimeout(timer); }, []);
        const project = switched
          ? { project: { id: 'second', name: 'Second', workspace_dir: ${JSON.stringify(second)} }, detection: { types: [] } }
          : { project: { id: 'first', name: 'First', workspace_dir: ${JSON.stringify(root)} }, detection: { types: [] } };
        const scope = { ownerId: switched ? 'owner-b' : 'owner-a', cacheKey: switched ? 'owner-b' : 'owner-a', runtimeOrigin: location.origin };
        return <><output id="owner">{scope.ownerId}</output><CodeFiles project={project} scope={scope} /></>;
      }
      createRoot(document.getElementById('root')).render(<Harness />);
    `
    const outputs = buildSync({ stdin: { contents: source, resolveDir: process.cwd(), loader: 'tsx' },
      bundle: true, platform: 'browser', format: 'iife', write: false, outfile: join(tmpdir(), 'gideon-code-files.js'),
      resolveExtensions: ['.web.tsx', '.web.ts', '.tsx', '.ts', '.js', '.json'],
      alias: { 'react-native': 'react-native-web' },
      define: { 'process.env.NODE_ENV': '"production"', __DEV__: 'false' }, logLevel: 'silent' }).outputFiles
    const script = outputs.find(file => file.path.endsWith('.js'))?.text
    const style = outputs.find(file => file.path.endsWith('.css'))?.text ?? ''
    if (!script) throw new Error('Code files browser bundle was not created')
    const html = `<!doctype html><html><head><style>${style}</style></head><body><div id="root"></div><script>${script}</script></body></html>`
    const web = createServer(async (request, response) => {
      if (request.url?.startsWith('/api/')) {
        try {
          const upstream = await originalFetch(new URL(request.url, origin))
          response.writeHead(upstream.status, Object.fromEntries(upstream.headers.entries()))
          response.end(Buffer.from(await upstream.arrayBuffer()))
        } catch { response.writeHead(502); response.end() }
        return
      }
      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); response.end(html)
    })
    await new Promise<void>(accept => web.listen(0, '127.0.0.1', accept))
    try {
      const address = web.address()
      if (!address || typeof address === 'string') throw new Error('Browser server unavailable')
      const { stdout } = await promisify(execFile)(process.env.CHROMIUM_BIN || 'chromium', [
        '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-background-networking',
        '--virtual-time-budget=2500', '--dump-dom', `http://127.0.0.1:${address.port}/`,
      ], { timeout: 30000, maxBuffer: 4_000_000 })
      expect(stdout).toContain('id="owner">owner-b')
      expect(stdout).toContain('Open file other.txt')
      expect(stdout).not.toContain('Open file notes.txt')
      expect(stdout).not.toContain('Open file binary.bin')
    } finally { await new Promise<void>(accept => web.close(() => accept())) }
  }, 40000)

  it('keeps an old-owner draft at its original key when a native rename finishes after owner switch', async () => {
    const source = `
      import * as React from 'react';
      import { createRoot } from 'react-dom/client';
      import { flushSync } from 'react-dom';
      import CodeFiles from './src/features/code/CodeFiles.web';
      const firstRoot = ${JSON.stringify(root)};
      const secondRoot = ${JSON.stringify(second)};
      const oldPath = firstRoot + '/binary.bin';
      const newPath = firstRoot + '/moved.bin';
      const key = path => 'gideon:code-draft:owner-a:first:' + encodeURIComponent(path);
      function waitFor(selector) { return new Promise((resolve, reject) => {
        let attempts = 0;
        const timer = setInterval(() => {
          const found = document.querySelector(selector);
          if (found) { clearInterval(timer); resolve(found); }
          if (++attempts > 200) { clearInterval(timer); reject(new Error('Missing ' + selector)); }
        }, 10);
      }); }
      function Harness() {
        const [switched, setSwitched] = React.useState(false);
        React.useEffect(() => {
          async function begin() {
            (await waitFor('[aria-label="Open file binary.bin"]')).click();
            await waitFor('h3');
            sessionStorage.setItem(key(oldPath), JSON.stringify({ path: oldPath, base: 'before', text: 'unsaved', validator: 'v1' }));
            (await waitFor('[aria-label="Rename binary.bin"]')).click();
            const input = await waitFor('#code-rename');
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'moved.bin');
            input.dispatchEvent(new Event('input', { bubbles: true }));
            await new Promise(resolve => setTimeout(resolve, 30));
            input.closest('form').requestSubmit();
            flushSync(() => setSwitched(true));
            setTimeout(() => {
              document.body.dataset.oldDraft = sessionStorage.hasOwnProperty(key(oldPath)) || sessionStorage.getItem(key(oldPath)) ? 'present' : 'missing';
              document.body.dataset.newDraft = sessionStorage.getItem(key(newPath)) ? 'present' : 'missing';
            }, 1100);
          }
          void begin();
        }, []);
        const project = switched
          ? { project: { id: 'second', name: 'Second', workspace_dir: secondRoot }, detection: { types: [] } }
          : { project: { id: 'first', name: 'First', workspace_dir: firstRoot }, detection: { types: [] } };
        const scope = { ownerId: switched ? 'owner-b' : 'owner-a', cacheKey: switched ? 'owner-b' : 'owner-a', runtimeOrigin: location.origin };
        return <><output id="owner">{scope.ownerId}</output><CodeFiles project={project} scope={scope} /></>;
      }
      createRoot(document.getElementById('root')).render(<Harness />);
    `
    const outputs = buildSync({ stdin: { contents: source, resolveDir: process.cwd(), loader: 'tsx' },
      bundle: true, platform: 'browser', format: 'iife', write: false, outfile: join(tmpdir(), 'gideon-code-rename.js'),
      resolveExtensions: ['.web.tsx', '.web.ts', '.tsx', '.ts', '.js', '.json'],
      alias: { 'react-native': 'react-native-web' },
      define: { 'process.env.NODE_ENV': '"production"', __DEV__: 'false' }, logLevel: 'silent' }).outputFiles
    const script = outputs.find(file => file.path.endsWith('.js'))?.text
    const style = outputs.find(file => file.path.endsWith('.css'))?.text ?? ''
    if (!script) throw new Error('Code rename browser bundle was not created')
    const html = `<!doctype html><html><head><style>${style}</style></head><body><div id="root"></div><script>${script}</script></body></html>`
    const web = createServer(async (request, response) => {
      if (request.url?.startsWith('/api/')) {
        try {
          const upstream = await originalFetch(new URL(request.url, origin), {
            method: request.method, headers: { 'Content-Type': request.headers['content-type'] || 'application/json' },
            body: request.method === 'POST' ? await new Promise<Buffer>(accept => {
              const chunks: Buffer[] = []; request.on('data', chunk => chunks.push(Buffer.from(chunk)))
              request.on('end', () => accept(Buffer.concat(chunks)))
            }).then(value => value.toString('utf8')) : undefined,
          })
          response.writeHead(upstream.status, Object.fromEntries(upstream.headers.entries()))
          response.end(Buffer.from(await upstream.arrayBuffer()))
        } catch { response.writeHead(502); response.end() }
        return
      }
      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); response.end(html)
    })
    await new Promise<void>(accept => web.listen(0, '127.0.0.1', accept))
    try {
      const address = web.address()
      if (!address || typeof address === 'string') throw new Error('Browser server unavailable')
      const { stdout } = await promisify(execFile)(process.env.CHROMIUM_BIN || 'chromium', [
        '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-background-networking',
        '--virtual-time-budget=3500', '--dump-dom', `http://127.0.0.1:${address.port}/`,
      ], { timeout: 30000, maxBuffer: 4_000_000 })
      expect(stdout.includes('id="owner">owner-b')).toBe(true)
      expect(stdout.includes('data-old-draft="present"')).toBe(true)
      expect(stdout.includes('data-new-draft="missing"')).toBe(true)
      expect((await readProjectFile(`${root}/moved.bin`, root)).binary).toBe(true)
    } finally { await new Promise<void>(accept => web.close(() => accept())) }
  }, 40000)
})
