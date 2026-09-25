// @vitest-environment node
import { afterAll, beforeAll, expect, test } from 'vitest'
import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
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
const cache = mkdtempSync(resolve(tmpdir(), 'workspace-logs-vite-'))

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

test('live logs pause resume filter and export actual managed process output', async () => {
  const response = await page.request.post(`${origin}/api/capabilities/workspace/processes`, { data: { project_id: 'live-log-app', workspace: repo, command: "printf 'first-line\\n'; while [ ! -f continue-logs ]; do sleep 0.1; done; printf 'second-line\\n'; exec sleep 30", request_id: 'live-log' } })
  expect(response.status()).toBe(200)
  const row = await response.json()
  await page.goto(`${origin}/workspace-test#/capabilities/workspace?view=processes&process=${row.id}`)
  const area = page.getByRole('region', { name: 'Live process log' })
  await area.getByRole('button', { name: 'Follow process log' }).click()
  await expect.poll(() => area.getByLabel('Live log output').textContent()).toContain('first-line')
  await area.getByRole('button', { name: 'Pause live log' }).click()
  expect(await area.getByLabel('Live log output').textContent()).not.toContain('second-line')
  writeFileSync(resolve(repo, 'continue-logs'), 'continue')
  await expect.poll(async () => (await (await page.request.get(`${origin}/api/capabilities/workspace/processes/${row.id}/logs`)).json()).text).toContain('second-line')
  const paused = await area.getByLabel('Live log output').textContent()
  await area.getByRole('button', { name: 'Follow process log' }).click()
  await expect.poll(() => area.getByLabel('Live log output').textContent()).toContain('second-line')
  expect(paused).toContain('first-line')
  expect(paused).not.toContain('second-line')
  await area.getByLabel('Filter retained log lines').fill('SECOND')
  expect(await area.getByLabel('Live log output').textContent()).toContain('second-line')
  expect(await area.getByLabel('Live log output').textContent()).not.toContain('first-line')
  const downloaded = page.waitForEvent('download')
  await area.getByRole('button', { name: 'Download retained log' }).click()
  const file = await downloaded
  expect(file.suggestedFilename()).toBe(`process-${row.id}.log`)
  const stream = await file.createReadStream()
  const chunks: Buffer[] = []
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk))
  expect(Buffer.concat(chunks).toString()).toBe('first-line\nsecond-line\n')
  await page.getByRole('button', { name: 'Stop process', exact: true }).click()
  await expect.poll(() => area.textContent()).toContain('Status: stopped')
  expect(await area.getByRole('button', { name: 'Follow process log' }).count()).toBe(1)
}, 30000)

test('retention gaps are visible and restarting observation does not duplicate output', async () => {
  const response = await page.request.post(`${origin}/api/capabilities/workspace/processes`, { data: { project_id: 'retained-log-app', workspace: repo, command: "python3 -c \"print('x'*70000+'tail-marker')\"", request_id: 'retained-log' } })
  expect(response.status()).toBe(200)
  const row = await response.json()
  await expect.poll(async () => (await (await page.request.get(`${origin}/api/capabilities/workspace/processes/${row.id}`)).json()).status).toBe('exited')
  await page.goto('about:blank')
  await page.goto(`${origin}/workspace-test#/capabilities/workspace?view=processes&process=${row.id}`)
  const area = page.getByRole('region', { name: 'Live process log' })
  await area.getByRole('button', { name: 'Follow process log' }).click()
  await expect.poll(() => area.getByLabel('Live log output').textContent()).toContain('tail-marker')
  const text = await area.getByLabel('Live log output').textContent()
  expect(text!.length).toBe(65536)
  expect(await area.textContent()).toContain('4476 characters missed')
  await area.getByRole('button', { name: 'Follow process log' }).click()
  await expect.poll(() => area.getByRole('button', { name: 'Follow process log' }).count()).toBe(1)
  expect(await area.getByLabel('Live log output').textContent()).toBe(text)
  await area.getByLabel('Filter retained log lines').fill('absent-filter')
  expect(await area.getByLabel('Live log output').textContent()).toBe('No matching output yet.')
}, 30000)
