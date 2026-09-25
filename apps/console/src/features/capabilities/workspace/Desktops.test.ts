// @vitest-environment node
import { afterAll, beforeAll, expect, test } from 'vitest'
import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { mkdtempSync, rmSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { createServer, type ViteDevServer } from 'vite'
import react from '@vitejs/plugin-react'
import { chromium, type Browser, type Page } from 'playwright'

let backend: ChildProcess
let server: ViteDevServer
let browser: Browser
let page: Page
let repo = ''
let token = ''
let origin = ''
const root = resolve(process.cwd(), '../..')
const cache = mkdtempSync(resolve(tmpdir(), 'workspace-desktops-vite-'))

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
  repo = ready.repo; token = ready.token
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

test('operates an actual isolated desktop terminal with real frames and stops owned children', async () => {
  const registered = await page.request.post(`${origin}/api/capabilities/workspace/projects`, { data: { name: 'Browser desktop project', workspace: repo, request_id: 'desktop-project' } })
  expect(registered.status()).toBe(200)
  const record = await registered.json()
  await page.goto(`${origin}/workspace-test#/capabilities/workspace?view=desktops`)
  await page.getByLabel('Desktop project', { exact: true }).selectOption(record.project.id)
  await page.getByRole('button', { name: 'Start isolated desktop', exact: true }).click()
  await page.getByText('Desktop status: running', { exact: true }).waitFor({ timeout: 60000 })
  expect(page.url()).toContain('desktop=')
  const frame = page.getByAltText('Isolated desktop frame', { exact: true })
  await frame.waitFor({ timeout: 30000 })
  await expect.poll(() => frame.evaluate((image: HTMLImageElement) => image.naturalWidth), { timeout: 30000 }).toBe(800)
  expect(await frame.evaluate((image: HTMLImageElement) => image.naturalHeight)).toBe(600)
  await frame.click({ position: { x: 100, y: 100 } })
  await page.getByLabel('Desktop text', { exact: true }).fill('printf browser-desktop > browser-desktop.txt')
  await page.getByRole('button', { name: 'Send text to desktop', exact: true }).click()
  await expect.poll(() => page.getByLabel('Desktop text', { exact: true }).inputValue(), { timeout: 30000 }).toBe('')
  await page.getByRole('button', { name: 'Return', exact: true }).click()
  const output = resolve(repo, 'browser-desktop.txt')
  await expect.poll(() => existsSync(output) ? readFileSync(output, 'utf8') : '', { timeout: 30000 }).toBe('browser-desktop')
  const selected = page.url()
  await page.reload()
  await page.getByText('Desktop status: running', { exact: true }).waitFor({ timeout: 30000 })
  expect(page.url()).toBe(selected)
  await page.getByRole('button', { name: 'Stop isolated desktop', exact: true }).click()
  await page.getByText('Desktop status: stopped', { exact: true }).waitFor({ timeout: 30000 })
  expect(await page.getByAltText('Isolated desktop frame').count()).toBe(0)
  const sessions = await page.request.get(`${origin}/api/capabilities/workspace/desktops`)
  const rows = await sessions.json()
  expect(rows).toHaveLength(1)
  expect(rows[0].status).toBe('stopped')
  expect(rows[0].ended_at).toBeTruthy()
  expect(rows[0]).not.toHaveProperty('display')
  expect(rows[0]).not.toHaveProperty('pid')
  expect(rows[0]).not.toHaveProperty('auth')
  expect(readFileSync(output, 'utf8')).toBe('browser-desktop')
}, 120000)

test('restores stopped desktop history without restarting it and refuses stale live access', async () => {
  await page.reload()
  await page.getByText('Desktop status: stopped', { exact: true }).waitFor()
  const sessionId = new URLSearchParams(page.url().split('?')[1]).get('desktop')
  expect(sessionId).toBeTruthy()
  const response = await page.request.get(`${origin}/api/capabilities/workspace/desktops/${sessionId}/frame`)
  expect(response.status()).toBe(409)
  expect((await response.json()).error).toContain('not running')
  expect(await page.getByRole('button', { name: 'Send text to desktop', exact: true }).count()).toBe(0)
  expect(await page.getByRole('button', { name: 'Stop isolated desktop', exact: true }).count()).toBe(0)
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.getByRole('button', { name: 'Start isolated desktop', exact: true }).isVisible()).toBe(true)
}, 60000)
