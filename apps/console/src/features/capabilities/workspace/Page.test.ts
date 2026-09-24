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
const cache = mkdtempSync(resolve(tmpdir(), 'workspace-vite-'))

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

test('saves, compares, reloads and deletes through actual authenticated HTTP', async () => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(`${origin}/workspace-test#/capabilities/workspace`)
  await page.getByText('No saved contexts.').waitFor()
  await page.getByLabel('Project ID', { exact: true }).fill('Browser project')
  await page.getByLabel('Workspace path', { exact: true }).fill(repo)
  await page.getByRole('button', { name: 'Save context', exact: true }).click()
  await page.getByRole('heading', { name: 'Browser project', exact: true }).waitFor()
  expect(page.url()).toContain('snapshot=')
  expect(await page.getByText('Saved branch: main', { exact: false }).textContent()).toContain('Clean')
  execFileSync('git', ['-C', repo, 'checkout', '-b', 'browser-next'])
  await page.getByRole('button', { name: 'Compare with live workspace' }).click()
  await page.getByText('Branch changed', { exact: false }).waitFor()
  expect(await page.getByText('Live branch:', { exact: false }).textContent()).toContain('browser-next')
  expect(execFileSync('git', ['-C', repo, 'branch', '--show-current']).toString().trim()).toBe('browser-next')
  await page.reload()
  await page.getByRole('heading', { name: 'Browser project', exact: true }).waitFor()
  expect(await page.getByText('Saved branch: main', { exact: false }).count()).toBe(1)
  await page.getByRole('button', { name: 'Delete saved context' }).click()
  await page.getByText('No saved contexts.').waitFor()
  expect(page.url()).not.toContain('snapshot=')
  expect(errors).toEqual([])
}, 60000)

test('retains actual server errors without discarding form inputs', async () => {
  await page.goto(`${origin}/workspace-test#/capabilities/workspace`)
  await page.getByLabel('Project ID', { exact: true }).fill('Invalid workspace')
  await page.getByLabel('Workspace path', { exact: true }).fill('/etc')
  await page.getByRole('button', { name: 'Save context', exact: true }).click()
  await page.getByRole('alert').waitFor()
  expect(await page.getByRole('alert').textContent()).toContain('outside allowed roots')
  expect(await page.getByLabel('Workspace path', { exact: true }).inputValue()).toBe('/etc')
  expect(await page.getByLabel('Project ID', { exact: true }).inputValue()).toBe('Invalid workspace')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.getByRole('button', { name: 'Save context', exact: true }).isVisible()).toBe(true)
  await page.getByLabel('Workspace path', { exact: true }).fill(repo)
  await page.getByRole('button', { name: 'Save context', exact: true }).click()
  await page.getByRole('heading', { name: 'Invalid workspace', exact: true }).waitFor()
  expect(await page.getByRole('alert').count()).toBe(0)
}, 60000)
