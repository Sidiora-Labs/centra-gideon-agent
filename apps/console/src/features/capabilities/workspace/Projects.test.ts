// @vitest-environment node
import { afterAll, beforeAll, expect, test } from 'vitest'
import { spawn, execFileSync, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { mkdtempSync, rmSync, readFileSync } from 'node:fs'
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
const cache = mkdtempSync(resolve(tmpdir(), 'workspace-projects-vite-'))

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

test('detects and registers a canonical project then restores selection on reload', async () => {
  await page.goto(`${origin}/workspace-test#/capabilities/workspace`)
  await page.getByText('No registered local projects.').waitFor()
  await page.getByLabel('Project display name').fill('Browser existing source')
  await page.getByLabel('Existing project path').fill(repo)
  await page.getByRole('button', { name: 'Detect project', exact: true }).click()
  await page.getByText('Git: Present', { exact: true }).waitFor()
  await page.getByRole('button', { name: 'Register project', exact: true }).click()
  await page.getByRole('heading', { name: 'Browser existing source', exact: true }).waitFor()
  expect(page.url()).toContain('project=p-')
  const selected = page.url()
  await page.reload()
  await page.getByRole('heading', { name: 'Browser existing source', exact: true }).waitFor()
  expect(page.url()).toBe(selected)
  const data = await page.request.get(`${origin}/api/capabilities/workspace/projects`)
  expect(data.status()).toBe(200)
  const rows = await data.json()
  expect(rows).toHaveLength(1)
  expect(rows[0].project.workspace_dir).toBe(repo)
  expect(rows[0].detection.has_git).toBe(true)
}, 60000)

test('scaffolds actual local files and keeps no-overwrite errors and inputs', async () => {
  await page.goto(`${origin}/workspace-test#/capabilities/workspace`)
  await page.getByLabel('Project display name').fill('Browser Python service')
  await page.getByLabel('Scaffold parent').fill(repo)
  await page.getByLabel('New directory').fill('browser-python')
  await page.getByLabel('Service template').selectOption('python-http')
  await page.getByRole('button', { name: 'Create project', exact: true }).click()
  await page.getByRole('heading', { name: 'Browser Python service', exact: true }).waitFor()
  const source = readFileSync(resolve(repo, 'browser-python/app.py'), 'utf8')
  expect(source).toContain('127.0.0.1')
  expect(source).toContain('/health')
  await page.reload()
  await page.getByRole('heading', { name: 'Browser Python service', exact: true }).waitFor()
  await page.getByLabel('Project display name').fill('Conflicting project')
  await page.getByLabel('Scaffold parent').fill(repo)
  await page.getByLabel('New directory').fill('browser-python')
  await page.getByRole('button', { name: 'Create project', exact: true }).click()
  await page.getByRole('alert').waitFor()
  expect(await page.getByRole('alert').textContent()).toContain('exists')
  expect(await page.getByLabel('New directory').inputValue()).toBe('browser-python')
  expect(readFileSync(resolve(repo, 'browser-python/app.py'), 'utf8')).toBe(source)
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.getByRole('button', { name: 'Create project', exact: true }).isVisible()).toBe(true)
}, 60000)
