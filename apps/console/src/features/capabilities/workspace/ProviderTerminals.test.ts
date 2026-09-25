// @vitest-environment node
import { afterAll, beforeAll, expect, test } from 'vitest'
import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { createServer, type ViteDevServer } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { chromium, type Browser, type Page } from 'playwright'

let backend: ChildProcess
let server: ViteDevServer
let browser: Browser
let page: Page
let repo = ''
let token = ''
let origin = ''
const wire: string[] = []
const events: string[] = []
let backendErrors = ''
const diagnostics = mkdtempSync(resolve(tmpdir(), 'workspace08-ui-diagnostics-'))
const root = resolve(process.cwd(), '../..')
const cache = mkdtempSync(resolve(tmpdir(), 'workspace-provider-vite-'))

beforeAll(async () => {
  backend = spawn('/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/workspace/provider_ui_server.py')], { env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  const ready = await new Promise<{ url: string; repo: string; token: string }>((accept, reject) => {
    let output = '', errors = ''
    backend.stderr!.on('data', chunk => { errors += chunk; backendErrors += chunk })
    backend.on('exit', code => reject(new Error(`Backend exited ${code}: ${errors}`)))
    backend.stdout!.on('data', chunk => {
      output += chunk
      const line = output.split('\n').find(text => text.startsWith('{"url"'))
      if (line) accept(JSON.parse(line))
    })
  })
  repo = ready.repo; token = ready.token
  server = await createServer({
    configFile: false, cacheDir: cache, root: process.cwd(),
    optimizeDeps: { noDiscovery: true, include: ['react', 'react-dom/client', 'react/jsx-runtime', '@xterm/xterm', '@xterm/addon-fit', '@xterm/addon-web-links', 'lucide-react'] },
    plugins: [react(), tailwindcss(), { name: 'workspace-real-browser-entry', configureServer(vite) {
      vite.middlewares.use(async (req, res, next) => {
        if (req.url?.split('?')[0] !== '/workspace-test') return next()
        const html = await vite.transformIndexHtml('/workspace-test', `<html><head><link rel="stylesheet" href="/src/shared/theme/tokens.css"></head><body><div id="root"></div><script type="module" src="/@fs/${root}/checks/runtime/capabilities/workspace/entry.tsx"></script></body></html>`)
        res.setHeader('Content-Type', 'text/html'); res.end(html)
      })
    } }],
    server: { fs: { allow: [root] }, host: '127.0.0.1', port: 0, proxy: { '/api': { target: ready.url, changeOrigin: true, ws: true } } },
  })
  await server.listen()
  const address = server.httpServer!.address()
  if (!address || typeof address === 'string') throw new Error('Missing server address')
  origin = `http://127.0.0.1:${address.port}`
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || '/opt/chromium/chrome-linux64/chrome', headless: true, args: ['--no-sandbox'] })
  page = await browser.newPage()
  page.on('pageerror', error => events.push(error.message))
  page.on('console', message => events.push(message.type() + ':' + message.text()))
  page.on('websocket', socket => { events.push('WS ' + socket.url()); socket.on('framereceived', event => wire.push('in:' + String(event.payload))); socket.on('framesent', event => wire.push('out:' + String(event.payload))) })
  await page.goto(`${origin}/api/capabilities/workspace?token=${token}`)
}, 60000)

afterAll(async () => {
  if (page) {
    writeFileSync(resolve(diagnostics, 'browser.json'), JSON.stringify({ wire, events, backendErrors, dom: await page.content(), geometry: await page.locator('[aria-label="Terminal session"]').evaluateAll(nodes => nodes.map(node => ({ rect: node.getBoundingClientRect().toJSON(), children: [...node.children].map(child => ({ rect: child.getBoundingClientRect().toJSON(), css: getComputedStyle(child).cssText })) }))) }, null, 2))
    await page.screenshot({ path: resolve(diagnostics, 'screen.png'), fullPage: true })
    console.log('Persisted browser diagnostics: ' + diagnostics)
  }
  await browser?.close()
  await server?.close()
  backend?.kill()
  rmSync(cache, { recursive: true, force: true })
})

test('configured provider receives real uploaded image through the existing interactive terminal', async () => {
  await page.goto(`${origin}/workspace-test#/capabilities/workspace?view=provider`)
  const area = page.getByRole('region', { name: 'Provider terminal', exact: true })
  await area.getByLabel('Terminal provider').selectOption('workspace-codex-test')
  await area.getByLabel('Provider working directory').fill(repo)
  await area.getByLabel('Provider startup image').setInputFiles({ name: 'pixel.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAwAAAAJCAIAAACJ2loDAAAAFklEQVR4nGP8v5SBIGAirGRUEQMDAwAtKwG2hpmzSgAAAABJRU5ErkJggg==', 'base64') })
  await area.getByRole('button', { name: 'Launch provider terminal' }).click()
  await area.getByRole('button', { name: 'Close provider terminal' }).waitFor({ timeout: 30000 })
  const terminal = area.locator('.xterm')
  await terminal.waitFor({ timeout: 30000 })
  expect(await area.getByRole('button', { name: 'Launch provider terminal' }).isDisabled()).toBe(true)
  const bounds = await area.getByRole('group', { name: 'Terminal session' }).boundingBox()
  expect(bounds!.y).toBeLessThan(720)
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(721)
  await expect.poll(async () => ({ text: await terminal.locator('.xterm-rows').textContent(), wire: wire.join('\n'), size: await area.getByRole('group', { name: 'Terminal session' }).boundingBox() }), { timeout: 30000 }).toMatchObject({ text: expect.stringMatching(/Welcome to|Sign in|sign in|ChatGPT/) })
  await area.getByRole('button', { name: 'Close provider terminal' }).click()
  await expect.poll(() => area.locator('.xterm').count(), { timeout: 30000 }).toBe(0)
  expect(await area.getByRole('button', { name: 'Launch provider terminal' }).isEnabled()).toBe(true)
}, 60000)

test('invalid image remains a visible retryable error without launching a terminal', async () => {
  await page.goto('about:blank')
  await page.goto(`${origin}/workspace-test#/capabilities/workspace?view=provider`)
  const area = page.getByRole('region', { name: 'Provider terminal', exact: true })
  await area.getByLabel('Terminal provider').selectOption('workspace-codex-test')
  await area.getByLabel('Provider working directory').fill(repo)
  await area.getByLabel('Provider startup image').setInputFiles({ name: 'wrong.png', mimeType: 'image/png', buffer: Buffer.from('not image data') })
  await area.getByRole('button', { name: 'Launch provider terminal' }).click()
  await area.getByRole('alert').waitFor()
  expect(await area.getByRole('alert').textContent()).toContain('Invalid image')
  expect(await area.locator('.xterm').count()).toBe(0)
  expect(await area.getByRole('button', { name: 'Launch provider terminal' }).isEnabled()).toBe(true)
  expect(await area.getByLabel('Provider working directory').inputValue()).toBe(repo)
  await page.setViewportSize({ width: 390, height: 844 })
  expect((await area.boundingBox())!.width).toBeLessThanOrEqual(390)
}, 30000)
