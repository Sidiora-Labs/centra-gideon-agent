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
const cache = mkdtempSync(resolve(tmpdir(), 'workspace-git-vite-'))

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

test('creates and switches real local branches through authenticated UI and reloads receipts', async () => {
  const registered = await page.request.post(`${origin}/api/capabilities/workspace/projects`, { data: { name: 'Browser Git source', workspace: repo, request_id: 'browser-git' } })
  expect(registered.status()).toBe(200)
  const record = await registered.json()
  await page.goto(`${origin}/workspace-test#/capabilities/workspace?view=git`)
  await page.getByLabel('Git project', { exact: true }).selectOption(record.project.id)
  await page.getByRole('button', { name: 'Inspect Git project', exact: true }).click()
  await page.getByText('Current branch: main', { exact: false }).waitFor()
  await page.getByLabel('Local branch name').fill('browser/feature')
  await page.getByRole('button', { name: 'Create and switch branch', exact: true }).click()
  await page.getByText('Current branch: browser/feature', { exact: false }).waitFor()
  expect(execFileSync('git', ['-C', repo, 'branch', '--show-current']).toString().trim()).toBe('browser/feature')
  expect(await page.getByLabel('Git operation history').textContent()).toContain('create_branch: browser/feature · succeeded')
  expect(page.url()).toContain('git_project=')
  await page.reload()
  await expect.poll(() => page.getByLabel('Git project', { exact: true }).inputValue(), { timeout: 30000 }).toBe(record.project.id)
  await page.getByRole('button', { name: 'Inspect Git project', exact: true }).click()
  await page.getByText('Current branch: browser/feature', { exact: false }).waitFor()
  await page.getByLabel('Local branch name').fill('main')
  await page.getByRole('button', { name: 'Switch existing branch', exact: true }).click()
  await page.getByText('Current branch: main', { exact: false }).waitFor()
  expect(execFileSync('git', ['-C', repo, 'branch', '--show-current']).toString().trim()).toBe('main')
  expect(await page.getByLabel('Git operation history').textContent()).toContain('switch_branch: main · succeeded')
}, 60000)

test('shows real operation failures and preserves branch input', async () => {
  await page.goto(`${origin}/workspace-test#/capabilities/workspace?view=git`)
  const response = await page.request.get(`${origin}/api/capabilities/workspace/projects`)
  const rows = await response.json()
  await page.getByLabel('Git project', { exact: true }).selectOption(rows[0].project.id)
  await page.getByRole('button', { name: 'Inspect Git project', exact: true }).click()
  await page.getByText('Current branch: main', { exact: false }).waitFor()
  await page.getByLabel('Local branch name').fill('missing-branch')
  await page.getByRole('button', { name: 'Switch existing branch', exact: true }).click()
  await page.getByRole('alert').waitFor()
  expect(await page.getByRole('alert').textContent()).toContain('Git operation failed')
  expect(await page.getByLabel('Local branch name').inputValue()).toBe('missing-branch')
  expect(execFileSync('git', ['-C', repo, 'branch', '--show-current']).toString().trim()).toBe('main')
  await page.getByRole('button', { name: 'Inspect Git project', exact: true }).click()
  await page.getByText('switch_branch: missing-branch · failed', { exact: true }).waitFor()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.getByRole('button', { name: 'Inspect Git project', exact: true }).isVisible()).toBe(true)
}, 60000)
