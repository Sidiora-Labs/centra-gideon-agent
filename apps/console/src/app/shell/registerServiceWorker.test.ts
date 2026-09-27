// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { build } from 'esbuild'
import { chromium } from 'playwright'
import { execFile, spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { mkdtemp, mkdir, readFile, writeFile, copyFile, rm } from 'node:fs/promises'
import { join, resolve, dirname } from 'node:path'
import { pathToFileURL } from 'node:url'
import { promisify } from 'node:util'
import { tmpdir } from 'node:os'
import { once } from 'node:events'
import { APP_SHELL } from './swPolicy'

const consoleRoot = resolve(import.meta.dirname, '../../..')
const repositoryRoot = resolve(consoleRoot, '../..')
const execFileAsync = promisify(execFile)
const buildServiceWorkerModule = pathToFileURL(join(consoleRoot, 'tooling/buildServiceWorker.mjs')).href

async function buildServiceWorker(webDir: string): Promise<{ version: string }> {
  const script = `import { buildServiceWorker } from ${JSON.stringify(buildServiceWorkerModule)}; console.log(JSON.stringify(await buildServiceWorker(process.argv[1])))`
  const { stdout } = await execFileAsync(process.execPath, ['--input-type=module', '-e', script, webDir], { cwd: consoleRoot })
  return JSON.parse(stdout) as { version: string }
}

async function fixture() {
  const temporary = await mkdtemp(join(tmpdir(), 'gideon-worker-'))
  const web = join(temporary, 'console')
  for (const source of ['app/background/service-worker.ts', 'app/shell/swPolicy.ts', 'app/shell/pushPolicy.ts']) {
    const target = join(web, 'src', source)
    await mkdir(dirname(target), { recursive: true })
    await copyFile(join(consoleRoot, 'src', source), target)
  }
  await mkdir(join(web, 'dist/assets'), { recursive: true })
  await mkdir(join(temporary, 'assistant/src'), { recursive: true })
  await writeFile(join(temporary, 'assistant/src/entry.ts'), 'export const generation = 1\n')
  for (const path of APP_SHELL.filter(path => path !== '/')) {
    const target = join(web, 'dist', path)
    await mkdir(dirname(target), { recursive: true })
    await copyFile(join(consoleRoot, 'public', path), target)
  }
  await writeFile(join(web, 'dist/assets/console.js'), 'globalThis.consoleAsset = true;')
  return { temporary, web }
}

describe('root worker distribution', () => {
  it('versions shell bytes and assistant sources even when their filenames do not change', async () => {
    const { temporary, web } = await fixture()
    try {
      const first = await buildServiceWorker(web)
      expect((await buildServiceWorker(web)).version).toBe(first.version)
      await writeFile(join(temporary, 'assistant/src/entry.ts'), 'export const generation = 2\n')
      const second = await buildServiceWorker(web)
      expect(second.version).not.toBe(first.version)
      await writeFile(join(web, 'dist/assets/console.js'), 'globalThis.consoleAsset = false;')
      expect((await buildServiceWorker(web)).version).not.toBe(second.version)
    } finally { await rm(temporary, { recursive: true, force: true }) }
  })

  it('keeps one root registration, separates offline documents, and never caches an authenticated owner', async () => {
    const { temporary, web } = await fixture()
    const registration = await build({
      stdin: { contents: `import { registerServiceWorker } from ${JSON.stringify(join(consoleRoot, 'src/app/shell/registerServiceWorker.ts'))}; window.workerRegistration = registerServiceWorker();`, resolveDir: consoleRoot },
      bundle: true, format: 'iife', write: false, define: { 'process.env.NODE_ENV': '"production"' },
    })
    const script = registration.outputFiles[0].text
    await writeFile(join(web, 'dist/index.html'), `<title>Gideon Console</title><h1>Console document</h1><script>${script}</script>`)
    await mkdir(join(web, 'dist/assistant/assets'), { recursive: true })
    await writeFile(join(web, 'dist/assistant/index.html'), `<title>Gideon Assistant</title><h1>Assistant document</h1><script>${script}</script>`)
    await writeFile(join(web, 'dist/assistant/assets/entry.js'), 'globalThis.assistantAsset = true;')
    await buildServiceWorker(web)
    let apiOrigin = ''
    const server = createServer(async (request, response) => {
      try {
        const pathname = new URL(request.url ?? '/', 'http://localhost').pathname
        if (pathname.startsWith('/api/')) {
          const chunks: Buffer[] = []
          for await (const chunk of request) chunks.push(Buffer.from(chunk))
          const headers = new Headers()
          for (const [key, value] of Object.entries(request.headers)) if (value && !['host', 'connection', 'content-length'].includes(key)) headers.set(key, Array.isArray(value) ? value.join(', ') : value)
          const upstream = await fetch(apiOrigin + request.url, { method: request.method, headers, body: chunks.length ? Buffer.concat(chunks) : undefined })
          const forwarded = Object.fromEntries(upstream.headers)
          delete forwarded['set-cookie']
          delete forwarded['content-length']
          response.writeHead(upstream.status, { ...forwarded, 'set-cookie': upstream.headers.getSetCookie() })
          response.end(Buffer.from(await upstream.arrayBuffer()))
          return
        }
        const file = pathname === '/' ? 'index.html' : /^\/assistant(?:\/(?:chat|apps)?)?$/.test(pathname) ? 'assistant/index.html' : pathname.slice(1)
        const path = resolve(web, 'dist', file)
        if (!path.startsWith(resolve(web, 'dist') + '/')) { response.writeHead(404); response.end(); return }
        const content = await readFile(path)
        const type = path.endsWith('.html') ? 'text/html' : path.endsWith('.js') ? 'text/javascript' : path.endsWith('.webmanifest') ? 'application/manifest+json' : path.endsWith('.svg') ? 'image/svg+xml' : path.endsWith('.png') ? 'image/png' : path.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream'
        response.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'no-store' })
        response.end(content)
      } catch { response.writeHead(404); response.end() }
    })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Missing HTTP test address')
    const origin = `http://127.0.0.1:${address.port}`
    const native = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', [join(repositoryRoot, 'apps/assistant/test-support/auth_server.py'), origin], {
      env: { ...process.env, GIDEON_HOME: join(temporary, 'native-home'), PYTHONPATH: join(repositoryRoot, 'runtime') },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let startup = ''; let errors = ''
    native.stdout.on('data', chunk => { startup += String(chunk) })
    native.stderr.on('data', chunk => { errors += String(chunk) })
    let browser: Awaited<ReturnType<typeof chromium.launch>> | undefined
    try {
      await expect.poll(() => startup.includes('\n') || native.exitCode !== null, { timeout: 20000 }).toBe(true)
      if (native.exitCode !== null) throw new Error(errors)
      apiOrigin = `http://127.0.0.1:${JSON.parse(startup.split('\n')[0]).api_port}`
      browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN, args: ['--no-sandbox'] })
      const context = await browser.newContext()
      const page = await context.newPage()
      await page.goto(origin)
      await page.evaluate(async () => { await navigator.serviceWorker.ready })
      await page.reload()
      await page.waitForFunction(() => navigator.serviceWorker.controller !== null)
      await page.goto(origin + '/assistant/chat')
      await page.waitForFunction(() => navigator.serviceWorker.controller !== null)
      expect(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).map(row => ({ scope: row.scope, updateViaCache: row.updateViaCache })))).toEqual([{ scope: origin + '/', updateViaCache: 'none' }])
      expect(await page.evaluate(async () => (await fetch('/manifest.webmanifest')).headers.get('content-type'))).toBe('application/manifest+json')
      expect(await page.evaluate(async () => (await fetch('/icons/icon-192.png')).headers.get('content-type'))).toBe('image/png')
      expect(await page.evaluate(async () => {
        const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'owner-a', password: 'correct-horse-battery-staple', totp: '' }) })
        return response.status
      })).toBe(200)
      expect(await page.evaluate(async () => (await (await fetch('/api/auth/session')).json()).user)).toBe('owner-a')
      await page.evaluate(async () => { await fetch('/assistant/assets/entry.js'); await fetch('/assets/console.js') })
      const cached = await page.evaluate(async () => (await Promise.all((await caches.keys()).map(async key => (await (await caches.open(key)).keys()).map(request => request.url)))).flat())
      expect(cached).toContain(origin + '/assistant/')
      expect(cached.some(url => url.includes('/api/'))).toBe(false)
      await page.evaluate(async () => { await fetch('/api/auth/logout', { method: 'POST' }) })
      expect(await page.evaluate(async () => (await fetch('/api/auth/session')).status)).toBe(403)
      await context.setOffline(true)
      await page.goto(origin + '/assistant/apps')
      expect(await page.locator('h1').textContent()).toBe('Assistant document')
      expect(await page.evaluate(async () => (await fetch('/assistant/assets/entry.js')).text())).toContain('assistantAsset')
      expect(await page.evaluate(async () => fetch('/api/auth/session').then(() => 'unexpected', () => 'offline'))).toBe('offline')
      await page.goto(origin)
      expect(await page.locator('h1').textContent()).toBe('Console document')
      await context.setOffline(false)
      await page.evaluate(async () => { await caches.open('gideon-shell-old'); await caches.open('application-owned') })
      await writeFile(join(temporary, 'assistant/src/entry.ts'), 'export const generation = 3\n')
      await buildServiceWorker(web)
      await page.evaluate(async () => { await (await navigator.serviceWorker.getRegistration())?.update() })
      await page.waitForFunction(async () => (await navigator.serviceWorker.getRegistration())?.waiting?.state === 'installed')
      expect(await page.evaluate(async () => (await caches.keys()).includes('gideon-shell-old'))).toBe(true)
      await page.goto('about:blank')
      await page.goto(origin)
      await page.waitForFunction(async () => !(await caches.keys()).includes('gideon-shell-old'))
      expect(await page.evaluate(async () => (await caches.keys()).includes('application-owned'))).toBe(true)
    } finally {
      await browser?.close()
      native.kill('SIGTERM')
      if (native.exitCode === null && native.signalCode === null) await once(native, 'exit')
      await new Promise<void>(resolveClose => server.close(() => resolveClose()))
      await rm(temporary, { recursive: true, force: true })
    }
  }, 60000)
})
