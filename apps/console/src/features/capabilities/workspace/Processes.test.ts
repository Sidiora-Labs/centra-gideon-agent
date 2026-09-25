// @vitest-environment node
import { afterAll, beforeAll, expect, test } from 'vitest'
import { spawn, execFileSync, type ChildProcess } from 'node:child_process'
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
let repo = ''
let token = ''
let origin = ''
const root = resolve(process.cwd(), '../..')
const cache = mkdtempSync(resolve(tmpdir(), 'workspace-process-vite-'))

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

test('starts a real process, reads output, stops and reloads its durable status', async () => {
  await page.goto(`${origin}/workspace-test#/capabilities/workspace`)
  await page.getByText('No managed processes.').waitFor()
  await page.getByLabel('Process project ID', { exact: true }).fill('Browser service')
  await page.getByLabel('Process workspace', { exact: true }).fill(repo)
  await page.getByLabel('Command', { exact: true }).fill('printf browser-ready; exec sleep 60')
  await page.getByRole('button', { name: 'Start process', exact: true }).click()
  await page.getByRole('heading', { name: 'Browser service', exact: true }).waitFor()
  expect(page.url()).toContain('process=')
  expect(await page.getByText('Status: running', { exact: false }).count()).toBe(1)
  await expect.poll(async () => {
    await page.getByRole('button', { name: 'Read process logs' }).click()
    await page.getByLabel('Process logs', { exact: true }).waitFor()
    return page.getByLabel('Process logs', { exact: true }).textContent()
  }, { timeout: 10000 }).toContain('browser-ready')
  await page.getByRole('button', { name: 'Stop process', exact: true }).click()
  await page.getByText('Status: stopped', { exact: false }).waitFor()
  expect(await page.getByRole('button', { name: 'Stop process', exact: true }).isDisabled()).toBe(true)
  await page.reload()
  await page.getByRole('heading', { name: 'Browser service', exact: true }).waitFor()
  expect(await page.getByText('Status: stopped', { exact: false }).count()).toBe(1)
  await page.getByRole('button', { name: 'Read process logs' }).click()
  expect(await page.getByLabel('Process logs', { exact: true }).textContent()).toContain('browser-ready')
}, 60000)

test('shows a genuine policy refusal and preserves the command for correction', async () => {
  await page.goto(`${origin}/workspace-test#/capabilities/workspace`)
  await page.getByLabel('Process project ID', { exact: true }).fill('Denied service')
  await page.getByLabel('Process workspace', { exact: true }).fill(repo)
  await page.getByLabel('Command', { exact: true }).fill('cat ~/.ssh/id_rsa')
  await page.getByRole('button', { name: 'Start process', exact: true }).click()
  await page.getByRole('alert').waitFor()
  expect(await page.getByRole('alert').textContent()).toContain('Blocked')
  expect(await page.getByLabel('Command', { exact: true }).inputValue()).toBe('cat ~/.ssh/id_rsa')
  expect(await page.getByRole('heading', { name: 'Denied service', exact: true }).count()).toBe(0)
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.getByRole('button', { name: 'Start process', exact: true }).isVisible()).toBe(true)
  await page.getByLabel('Command', { exact: true }).fill('printf corrected; exec sleep 60')
  await page.getByRole('button', { name: 'Start process', exact: true }).click()
  await page.getByRole('heading', { name: 'Denied service', exact: true }).waitFor()
  expect(await page.getByRole('alert').count()).toBe(0)
  await page.getByRole('button', { name: 'Stop process', exact: true }).click()
  await page.getByText('Status: stopped', { exact: false }).waitFor()
}, 60000)
