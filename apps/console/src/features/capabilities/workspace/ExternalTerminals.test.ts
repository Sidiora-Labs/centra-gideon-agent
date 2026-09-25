// @vitest-environment node
import { afterAll, beforeAll, expect, test } from 'vitest'
import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { createServer, type ViteDevServer } from 'vite'
import react from '@vitejs/plugin-react'
import { chromium, type Browser, type Page } from 'playwright'

let backend: ChildProcess
let server: ViteDevServer
let browser: Browser
let page: Page
let token = ''
let origin = ''
const root = resolve(process.cwd(), '../..')
const cache = mkdtempSync(resolve(tmpdir(), 'workspace-iterm-vite-'))

beforeAll(async () => {
  backend = spawn('/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/workspace/ui_server.py')], { env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  const ready = await new Promise<{ url: string; repo: string; token: string }>((accept, reject) => {
    let output = '', errors = ''
    backend.stderr!.on('data', chunk => { errors += chunk })
    backend.on('exit', code => reject(new Error(`Backend exited ${code}: ${errors}`)))
    backend.stdout!.on('data', chunk => {
      output += chunk
      const line = output.split('\n').find(text => text.startsWith('{"url"'))
      if (line) accept(JSON.parse(line))
    })
  })
  token = ready.token
  server = await createServer({
    configFile: false, cacheDir: cache, root: process.cwd(),
    optimizeDeps: { entries: [resolve(root, 'checks/runtime/capabilities/workspace/entry.tsx')] },
    plugins: [react(), { name: 'workspace-real-browser-entry', configureServer(vite) {
      vite.middlewares.use(async (req, res, next) => {
        if (req.url?.split('?')[0] !== '/workspace-test') return next()
        const html = await vite.transformIndexHtml('/workspace-test', `<html><body><div id="root"></div><script type="module" src="/@fs/${root}/checks/runtime/capabilities/workspace/entry.tsx"></script></body></html>`)
        res.setHeader('Content-Type', 'text/html'); res.end(html)
      })
    } }],
    server: { fs: { allow: [root] }, host: '127.0.0.1', port: 0, proxy: { '/api': { target: ready.url, changeOrigin: true } } },
  })
  await server.listen()
  const address = server.httpServer!.address()
  if (!address || typeof address === 'string') throw new Error('Missing server address')
  origin = `http://127.0.0.1:${address.port}`
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || '/opt/chromium/chrome-linux64/chrome', headless: true, args: ['--no-sandbox'] })
  page = await browser.newPage()
  page.on('pageerror', error => console.error(error.message))
  await page.goto(`${origin}/api/capabilities/workspace?token=${token}`)
}, 60000)

afterAll(async () => {
  await browser?.close()
  await server?.close()
  backend?.kill()
  rmSync(cache, { recursive: true, force: true })
})

test('native mirror reports actual platform limitation and keeps observation opt in', async () => {
  await page.goto(`${origin}/workspace-test#/capabilities/workspace?view=external`)
  const area = page.getByRole('region', { name: 'External terminal mirror' })
  expect(await area.getByRole('heading').textContent()).toBe('External terminal mirror')
  expect(await area.getByRole('button', { name: 'Observe pane' }).isDisabled()).toBe(true)
  expect(await area.getByLabel('Native pane', { exact: true }).inputValue()).toBe('')
  await area.getByRole('button', { name: 'Discover native panes' }).click()
  await area.getByRole('alert').waitFor()
  expect(await area.getByRole('alert').textContent()).toContain('macOS')
  expect(await area.getByLabel('Native pane output').count()).toBe(0)
  expect(await area.getByRole('button', { name: 'Discover native panes' }).isEnabled()).toBe(true)
  const response = await page.request.get(`${origin}/api/capabilities/workspace/external-terminals/availability`)
  expect(response.status()).toBe(200)
  expect((await response.json()).available).toBe(false)
  await area.getByRole('button', { name: 'Discover native panes' }).click()
  await expect.poll(() => area.getByRole('button', { name: 'Discover native panes' }).isEnabled()).toBe(true)
  expect(await area.getByRole('alert').textContent()).toContain('authorization')
}, 30000)

test('URL selection requires explicit observation and stale native content is not invented', async () => {
  await page.goto('about:blank')
  await page.goto(`${origin}/workspace-test#/capabilities/workspace?view=external&pane=missing-pane`)
  const area = page.getByRole('region', { name: 'External terminal mirror' })
  await area.getByRole('button', { name: 'Observe pane' }).waitFor()
  expect(await area.getByRole('alert').count()).toBe(0)
  expect(await area.getByLabel('Native pane output').count()).toBe(0)
  await area.getByRole('button', { name: 'Observe pane' }).click()
  await area.getByRole('alert').waitFor()
  expect(await area.getByRole('alert').textContent()).toContain('macOS')
  expect(await area.getByRole('button', { name: 'Disconnect mirror' }).count()).toBe(0)
  expect(page.url()).toContain('pane=missing-pane')
  await page.reload()
  await area.getByRole('button', { name: 'Observe pane' }).waitFor()
  expect(await area.getByRole('alert').count()).toBe(0)
  await page.setViewportSize({ width: 390, height: 844 })
  const bounds = await area.boundingBox()
  expect(bounds!.width).toBeLessThanOrEqual(390)
  const mutation = await page.request.post(`${origin}/api/capabilities/workspace/external-terminals/missing-pane`, { data: { command: 'echo denied' } })
  expect(mutation.status()).toBe(405)
}, 30000)
