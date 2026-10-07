// @vitest-environment node
import { afterAll, beforeAll, expect, test } from 'vitest'
import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { mkdtempSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs'
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
  backend = spawn(process.env.GIDEON_TEST_PYTHON || resolve(root, '.venv/bin/python'), [resolve(root, 'checks/runtime/capabilities/workspace/ui_server.py')], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  const ready = await new Promise<{ url: string; repo: string; token: string }>((accept, reject) => {
    let output = '', errors = ''
    const timeout = setTimeout(() => reject(new Error(`Workspace backend readiness timed out: ${errors}`)), 20000)
    backend.once('error', error => { clearTimeout(timeout); reject(error) })
    backend.stderr!.on('data', chunk => { errors += chunk })
    backend.on('exit', code => { clearTimeout(timeout); reject(new Error(`Backend exited ${code}: ${errors}`)) })
    backend.stdout!.on('data', chunk => {
      output += chunk
      const line = output.split('\n').find(text => text.startsWith('{"url"'))
      if (line) { clearTimeout(timeout); accept(JSON.parse(line)) }
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
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH, headless: true, args: ['--no-sandbox'] })
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
  await page.goto(`${origin}/workspace-test#/capabilities/workspace?view=storage`)
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
  await page.goto(`${origin}/workspace-test#/capabilities/workspace?view=storage`)
  const region = page.getByRole('region', { name: 'Storage diagnosis' })
  await region.getByText('Gideon runtime:', { exact: false }).waitFor()
  type ProjectUsage = { name: string; workspace_usage: { bytes: number; files: number; skipped_symlinks: number; complete: boolean } }
  const baselineResponse = await page.request.get(`${origin}/api/capabilities/workspace/storage`)
  expect(baselineResponse.status()).toBe(200)
  const baseline: { projects: ProjectUsage[] } = await baselineResponse.json()
  const before = baseline.projects.find(item => item.name === 'Storage browser project')!
  expect(before.workspace_usage.complete).toBe(true)
  const later = resolve(repo, 'later.bin')
  writeFileSync(later, Buffer.alloc(8192, 9))
  expect(statSync(later).size).toBe(8192)
  const refreshedResponse = page.waitForResponse(response => response.url().endsWith('/api/capabilities/workspace/storage') && response.request().method() === 'GET')
  await region.getByRole('button', { name: 'Refresh storage usage' }).click()
  const response = await refreshedResponse
  expect(response.status()).toBe(200)
  const refreshed: { projects: ProjectUsage[] } = await response.json()
  const after = refreshed.projects.find(item => item.name === 'Storage browser project')!
  expect(after.workspace_usage.complete).toBe(true)
  expect(after.workspace_usage.bytes - before.workspace_usage.bytes).toBe(8192)
  expect(after.workspace_usage.files - before.workspace_usage.files).toBe(1)
  expect(after.workspace_usage.skipped_symlinks).toBe(before.workspace_usage.skipped_symlinks)
  const formattedSize = await page.evaluate(bytes => new Intl.NumberFormat(undefined, { style: 'unit', unit: 'byte', notation: 'compact', unitDisplay: 'narrow' }).format(bytes), after.workspace_usage.bytes)
  const projectArticle = region.getByRole('article').filter({ hasText: 'Storage browser project' })
  await projectArticle.getByText(`Workspace: ${formattedSize}`, { exact: true }).waitFor()
  await page.route('**/api/capabilities/workspace/storage', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'Storage diagnosis unavailable' }) }))
  await region.getByRole('button', { name: 'Refresh storage usage' }).click()
  const alert = region.getByRole('alert')
  await alert.waitFor()
  expect(await alert.textContent()).toContain('Storage diagnosis unavailable')
}, 60000)
