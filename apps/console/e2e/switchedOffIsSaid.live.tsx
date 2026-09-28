// @vitest-environment node
import { afterAll, beforeAll, describe, expect, test } from 'vitest'
import { spawn, type ChildProcess } from 'node:child_process'
import { createServer as createTcpServer } from 'node:net'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { tmpdir } from 'node:os'
import { chromium, type Browser, type Page } from 'playwright'
import { createServer as createViteServer, type ViteDevServer } from 'vite'

const sourceDir = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(sourceDir, '../../..')
const consoleRoot = resolve(repoRoot, 'apps/console')
const runtimeRoot = process.env.GIDEON_TEST_RUNTIME_ROOT || resolve(repoRoot, 'runtime')
const gatewayPython = process.env.GIDEON_TEST_PYTHON || resolve(repoRoot, '.venv/bin/python')
const scratch = mkdtempSync(join(tmpdir(), 'gideon-switched-off-'))
const home = join(scratch, 'home')
const workspace = join(home, 'workspace')
let gatewayPort = 0
let gateway: ChildProcess | undefined
let gatewayToken = ''
let gatewayStderr = ''
let vite: ViteDevServer | undefined
let browser: Browser | undefined
let page: Page | undefined
let origin = ''
const apiResponses: Array<{ method: string; path: string; status: number }> = []
const browserFailures: string[] = []

async function unusedPort(): Promise<number> {
  const probe = createTcpServer()
  await new Promise<void>((resolveListen, reject) => probe.once('error', reject).listen(0, '127.0.0.1', resolveListen))
  const address = probe.address()
  if (!address || typeof address === 'string') throw new Error('Could not reserve an isolated test port')
  const port = address.port
  await new Promise<void>((resolveClose, reject) => probe.close((error) => error ? reject(error) : resolveClose()))
  return port
}

async function startGateway(): Promise<void> {
  gateway = spawn(gatewayPython, ['-m', 'gideon', 'gateway', '--port', String(gatewayPort), '--no-open', '--json-ready'], {
    cwd: repoRoot,
    env: {
      PATH: process.env.PATH ?? '/usr/bin:/bin', HOME: home, GIDEON_HOME: home,
      GIDEON_WORKSPACE: workspace, PYTHONPATH: runtimeRoot,
      XDG_CONFIG_HOME: join(home, '.config'), XDG_CACHE_HOME: join(home, '.cache'),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  gatewayStderr = ''
  gateway.stderr!.on('data', (chunk: Buffer) => { gatewayStderr += chunk.toString() })
  gatewayToken = await new Promise<string>((resolveReady, rejectReady) => {
    let pending = ''
    const timeout = setTimeout(() => rejectReady(new Error('Isolated Gideon gateway did not become ready')), 90_000)
    gateway!.once('exit', (code, signal) => {
      clearTimeout(timeout)
      rejectReady(new Error(`Isolated Gideon gateway exited before readiness (${code}/${signal}): ${gatewayStderr.slice(-4000)}`))
    })
    gateway!.stdout!.on('data', (chunk: Buffer) => {
      pending += chunk.toString()
      const line = pending.split(/\r?\n/).find((item) => item.startsWith('GIDEON_READY:'))
      if (!line) return
      try {
        const ready = JSON.parse(line.slice('GIDEON_READY:'.length)) as { token?: string }
        if (!ready.token) throw new Error('Missing readiness token')
        clearTimeout(timeout)
        resolveReady(ready.token)
      } catch {
        clearTimeout(timeout)
        rejectReady(new Error('Gateway returned an invalid readiness record'))
      }
    })
  })
}

async function stopGateway(): Promise<void> {
  const child = gateway
  if (!child || child.exitCode !== null) return
  await new Promise<void>((resolveStop) => {
    const timeout = setTimeout(() => child.kill('SIGKILL'), 10_000)
    child.once('exit', () => { clearTimeout(timeout); resolveStop() })
    child.kill('SIGTERM')
  })
  gateway = undefined
}

beforeAll(async () => {
  if (!existsSync(gatewayPython)) throw new Error('The isolated Gideon runtime Python executable is unavailable')
  mkdirSync(workspace, { recursive: true })
  writeFileSync(join(home, 'config.json'), JSON.stringify({
    dashboard: { user_name: 'Ada Lovelace', username: 'ada-lovelace' },
    resilience: { doctor_enabled: false },
    feedback: { enabled: false },
    learning: { enabled: true },
    rooms: { enabled: false },
    evals: { enabled: true },
  }))
  gatewayPort = await unusedPort()
  await startGateway()
  const previousPort = process.env.GIDEON_PORT
  process.env.GIDEON_PORT = String(gatewayPort)
  try {
    vite = await createViteServer({
      configFile: resolve(consoleRoot, 'vite.config.ts'),
      root: consoleRoot,
      cacheDir: join(scratch, 'vite-cache'),
      server: { host: '127.0.0.1', port: 0, strictPort: true, hmr: false },
      logLevel: 'info',
    })
  } finally {
    if (previousPort === undefined) delete process.env.GIDEON_PORT
    else process.env.GIDEON_PORT = previousPort
  }
  await vite.listen()
  const address = vite.httpServer?.address()
  if (!address || typeof address === 'string') throw new Error('The isolated console did not bind')
  origin = `http://127.0.0.1:${address.port}`
  browser = await chromium.launch({
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {}),
    headless: true, args: ['--no-sandbox'],
  })
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  page.on('response', (response) => {
    const url = new URL(response.url())
    if (url.pathname.startsWith('/api/')) apiResponses.push({ method: response.request().method(), path: url.pathname, status: response.status() })
  })
  page.on('pageerror', (error) => browserFailures.push(error.message.slice(0, 300)))
  page.on('console', (message) => { if (message.type() === 'error') browserFailures.push(message.text().slice(0, 300)) })
  page.on('requestfailed', (request) => browserFailures.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText ?? 'failed'}`.slice(0, 300)))
  const documentResponse = await fetch(origin, { signal: AbortSignal.timeout(15_000) })
  if (!documentResponse.ok) throw new Error(`Console document returned HTTP ${documentResponse.status}`)
  await documentResponse.body?.cancel()
}, 120_000)

afterAll(async () => {
  await browser?.close()
  await vite?.close()
  await stopGateway()
  rmSync(scratch, { recursive: true, force: true })
})

describe('switched-off and never-run states through the real gateway', () => {
  test('settings says Doctor and feedback are off; Learning names a never-run report', async () => {
    const currentPage = page!
    try {
      await currentPage.goto(`${origin}/?token=${encodeURIComponent(gatewayToken)}`, { waitUntil: 'commit', timeout: 30_000 })
      await currentPage.locator('nav[data-tour="rail"]').waitFor({ timeout: 30_000 })

      await currentPage.goto(`${origin}/#/settings/doctor`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
      await currentPage.getByRole('heading', { name: 'Doctor is off', exact: true }).waitFor({ timeout: 30_000 })
      expect(await currentPage.getByRole('alert').count()).toBe(0)
      expect(await currentPage.getByRole('button', { name: 'Turn the Doctor on', exact: true }).count()).toBe(1)

      await currentPage.goto(`${origin}/#/settings/feedback`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
      await currentPage.getByText('Feedback is off, so no 👍/👎 are shown', { exact: true }).waitFor({ timeout: 30_000 })
      expect(await currentPage.getByRole('alert').count()).toBe(0)
      expect(await currentPage.getByRole('switch', { name: 'Collect feedback' }).count()).toBe(1)

      await currentPage.goto(`${origin}/#/learning`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
      await currentPage.getByText('No ablation has run yet.', { exact: true }).waitFor({ timeout: 60_000 })
      expect(await currentPage.getByText(/Register a component in/).count()).toBe(1)
      expect(apiResponses).toContainEqual(expect.objectContaining({ method: 'GET', path: '/api/evals/ablation', status: 200 }))
      expect(browserFailures).toEqual([])
      expect(apiResponses).toContainEqual(expect.objectContaining({ method: 'GET', path: '/api/doctor', status: 200 }))
      expect(apiResponses).toContainEqual(expect.objectContaining({ method: 'GET', path: '/api/feedback/producers', status: 200 }))
    } catch (failure) {
      const evidenceDir = process.env.GIDEON_TEST_EVIDENCE_DIR
      if (evidenceDir) {
        mkdirSync(evidenceDir, { recursive: true, mode: 0o700 })
        const evidence = {
          failure: failure instanceof Error ? failure.message : String(failure),
          url: currentPage.url(),
          title: await currentPage.title().catch(() => ''),
          body: await currentPage.locator('body').innerText().catch(() => ''),
          html: await currentPage.content().catch(() => ''),
          railCount: await currentPage.locator('nav[data-tour="rail"]').count().catch(() => 0),
          apiResponses,
          browserFailures,
        }
        writeFileSync(join(evidenceDir, 'browser-failure.json'), JSON.stringify(evidence, null, 2), { mode: 0o600 })
        await currentPage.screenshot({ path: join(evidenceDir, 'browser-failure.png'), fullPage: true }).catch(() => undefined)
      }
      throw failure
    }
  }, 120_000)
})
