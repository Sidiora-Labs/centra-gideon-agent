// @vitest-environment node
import { afterAll, beforeAll, expect, test } from 'vitest'
import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { createServer, type ViteDevServer } from 'vite'
import react from '@vitejs/plugin-react'
import { chromium, type Browser, type Page } from 'playwright'

let backend: ChildProcess
let server: ViteDevServer
let browser: Browser
let page: Page
let repo = ''
let origin = ''
const root = resolve(process.cwd(), '../..')
const cache = mkdtempSync(resolve(tmpdir(), 'workspace-storage-vite-'))

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
  repo = ready.repo
  writeFileSync(resolve(repo, 'storage-payload.bin'), Buffer.alloc(4096, 7))
  symlinkSync(resolve(repo, 'storage-payload.bin'), resolve(repo, 'excluded-link.bin'))
  server = await createServer({
    configFile: false, cacheDir: cache, root: process.cwd(),
    optimizeDeps: { entries: [resolve(root, 'checks/runtime/capabilities/workspace/entry.tsx')] },
    plugins: [react(), { name: 'workspace-storage-entry', configureServer(vite) {
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
  await page.goto(`${origin}/api/capabilities/workspace?token=${ready.token}`)
  const registration = await page.request.post(`${origin}/api/capabilities/workspace/projects`, { data: { name: 'Storage browser project', workspace: repo, request_id: 'storage-browser-register' } })
  expect(registration.status()).toBe(200)
}, 60000)

afterAll(async () => {
  await browser?.close()
  await server?.close()
  backend?.kill()
  rmSync(cache, { recursive: true, force: true })
})

test('renders owned runtime and canonical project storage with symlink exclusion', async () => {
  await page.goto(`${origin}/workspace-test#/capabilities/workspace`)
  const region = page.getByRole('region', { name: 'Storage diagnosis' })
  await region.getByText('Gideon runtime:', { exact: false }).waitFor()
  const projectArticle = region.getByRole('article').filter({ hasText: 'Storage browser project' })
  await projectArticle.getByRole('heading', { name: 'Storage browser project' }).waitFor()
  await projectArticle.getByText(repo, { exact: true }).waitFor()
  await projectArticle.getByText('1 symlinks excluded', { exact: false }).waitFor()
  const response = await page.request.get(`${origin}/api/capabilities/workspace/storage`)
  expect(response.status()).toBe(200)
  const body = await response.json()
  const project = body.projects.find((item: { name: string }) => item.name === 'Storage browser project')
  expect(project.workspace_usage.bytes).toBeGreaterThanOrEqual(4096)
  expect(project.workspace_usage.skipped_symlinks).toBe(1)
})

test('refreshes the real projection and keeps server errors visible', async () => {
  await page.goto(`${origin}/workspace-test#/capabilities/workspace`)
  const region = page.getByRole('region', { name: 'Storage diagnosis' })
  await region.getByText('Gideon runtime:', { exact: false }).waitFor()
  writeFileSync(resolve(repo, 'later.bin'), Buffer.alloc(8192, 9))
  await region.getByRole('button', { name: 'Refresh storage usage' }).click()
  await expect.poll(async () => {
    const text = await region.textContent()
    return text?.includes('12') || text?.includes('13')
  }).toBe(true)
  await page.route('**/api/capabilities/workspace/storage', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'Storage diagnosis unavailable' }) }))
  await region.getByRole('button', { name: 'Refresh storage usage' }).click()
  const alert = region.getByRole('alert')
  await alert.waitFor()
  expect(await alert.textContent()).toContain('Storage diagnosis unavailable')
}, 60000)
