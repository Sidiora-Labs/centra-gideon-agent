// @vitest-environment node
import { afterAll, beforeAll, describe, expect, test } from 'vitest'
import { spawn, type ChildProcess } from 'node:child_process'
import { createServer as createTcpServer } from 'node:net'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { tmpdir } from 'node:os'
import { chromium, type Browser, type Page } from 'playwright'
import { createServer as createViteServer, type ViteDevServer } from 'vite'
import ts from 'typescript'
import { identityReducer, initialIdentity } from './identityState'

const sourceDir = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(sourceDir, '../../../../..')
const consoleRoot = resolve(repoRoot, 'apps/console')
const runtimeRoot = process.env.GIDEON_TEST_RUNTIME_ROOT || resolve(repoRoot, 'runtime')
const gatewayPython = process.env.GIDEON_TEST_PYTHON || resolve(repoRoot, '.venv/bin/python')
const scratch = mkdtempSync(join(tmpdir(), 'gideon-identity-read-'))
const home = join(scratch, 'home')
const workspace = join(home, 'workspace')
const viteCache = join(scratch, 'vite-cache')
const evidenceDir = mkdtempSync(join(tmpdir(), 'gideon-identity-browser-evidence-'))
let gatewayPort = 0
let gateway: ChildProcess | undefined
let gatewayToken = ''
let gatewayStderr = ''
let vite: ViteDevServer | undefined
let browser: Browser | undefined
let page: Page | undefined
let origin = ''
const requests: Array<{ method: string; path: string }> = []
let browserFailures: string[] = []
const apiResponses: Array<{ method: string; path: string; status: number }> = []
let navigationPhase = 'startup'
const pendingResources = new Map<object, { type: string; path: string }>()
const resourceResponses: Array<{ phase: string; type: string; path: string; status: number }> = []

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
  const env = {
    PATH: process.env.PATH ?? '/usr/bin:/bin',
    HOME: home,
    GIDEON_HOME: home,
    GIDEON_WORKSPACE: workspace,
    PYTHONPATH: runtimeRoot,
    XDG_CONFIG_HOME: join(home, '.config'),
    XDG_CACHE_HOME: join(home, '.cache'),
  }
  gateway = spawn(gatewayPython, ['-m', 'gideon', 'gateway', '--port', String(gatewayPort), '--no-open', '--json-ready'], {
    cwd: repoRoot, env, stdio: ['ignore', 'pipe', 'pipe'],
  })
  gatewayStderr = ''
  gateway.stderr!.on('data', (chunk: Buffer) => { gatewayStderr += chunk.toString() })
  gatewayToken = await new Promise<string>((resolveReady, rejectReady) => {
    let pending = ''
    const timeout = setTimeout(() => rejectReady(new Error('Isolated Gideon gateway did not become ready')), 90_000)
    gateway!.once('exit', (code, signal) => {
      clearTimeout(timeout)
      rejectReady(new Error(`Isolated Gideon gateway exited before readiness (code ${code}, signal ${signal}). stderr: ${gatewayStderr.slice(-5000)}`))
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
        rejectReady(new Error('Isolated Gideon gateway returned an invalid readiness record'))
      }
    })
  })
}

async function stopGateway(): Promise<void> {
  const child = gateway
  if (!child || child.exitCode !== null) return
  await new Promise<void>((resolveStop) => {
    const timeout = setTimeout(() => { child.kill('SIGKILL') }, 10_000)
    child.once('exit', () => { clearTimeout(timeout); resolveStop() })
    child.kill('SIGTERM')
  })
  gateway = undefined
  gatewayToken = ''
}

async function warmConsoleModules(server: ViteDevServer): Promise<void> {
  const pending = ['/src/app/bootstrap/main.tsx']
  const visited = new Set<string>()
  while (pending.length) {
    const batch = pending.splice(0, 8).filter(url => {
      if (visited.has(url)) return false
      visited.add(url)
      return true
    })
    await Promise.all(batch.map(async url => {
      const transformed = await server.transformRequest(url)
      if (!transformed) throw new Error('Console module transformation returned no source')
      const source = ts.createSourceFile(url, transformed.code, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS)
      for (const statement of source.statements) {
        if (!ts.isImportDeclaration(statement) && !ts.isExportDeclaration(statement)) continue
        const specifier = statement.moduleSpecifier
        if (!specifier || !ts.isStringLiteral(specifier)) continue
        const child = await server.moduleGraph.getModuleByUrl(specifier.text)
        if (child && !visited.has(child.url)) pending.push(child.url)
      }
    }))
  }
  await server.waitForRequestsIdle()
}

beforeAll(async () => {
  if (!existsSync(gatewayPython)) throw new Error('The isolated Gideon runtime Python executable is unavailable')
  mkdirSync(workspace, { recursive: true })
  mkdirSync(evidenceDir, { recursive: true })
  writeFileSync(join(home, 'config.json'), JSON.stringify({ dashboard: { user_name: 'Ada Lovelace', username: 'ada-lovelace' } }))
  gatewayPort = await unusedPort()
  await startGateway()
  const previousPort = process.env.GIDEON_PORT
  process.env.GIDEON_PORT = String(gatewayPort)
  try {
    vite = await createViteServer({
      configFile: resolve(consoleRoot, 'vite.config.ts'),
      cacheDir: viteCache,
      server: { host: '127.0.0.1', port: 0, strictPort: true, hmr: false },
      logLevel: 'info',
    })
  } finally {
    if (previousPort === undefined) delete process.env.GIDEON_PORT
    else process.env.GIDEON_PORT = previousPort
  }
  await vite.listen()
  await vite.warmupRequest('/src/app/bootstrap/main.tsx')
  await vite.waitForRequestsIdle()
  await warmConsoleModules(vite)
  const address = vite.httpServer?.address()
  if (!address || typeof address === 'string') throw new Error('The isolated Gideon console did not bind')
  origin = `http://127.0.0.1:${address.port}`
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
  browser = await chromium.launch({
    ...(executablePath ? { executablePath } : {}),
    headless: true,
    args: ['--no-sandbox'],
  })
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/')) requests.push({ method: request.method(), path: url.pathname })
    if (url.origin === origin && ['document', 'script', 'stylesheet'].includes(request.resourceType())) {
      pendingResources.set(request, { type: request.resourceType(), path: url.pathname })
    }
  })
  browserFailures = []
  page.on('requestfinished', (request) => pendingResources.delete(request))
  page.on('requestfailed', (request) => {
    pendingResources.delete(request)
    browserFailures.push(`${new URL(request.url()).pathname}: ${request.failure()?.errorText ?? 'request failed'}`)
  })
  page.on('response', (response) => {
    const url = new URL(response.url())
    if (url.pathname.startsWith('/api/')) apiResponses.push({ method: response.request().method(), path: url.pathname, status: response.status() })
    const type = response.request().resourceType()
    if (url.origin === origin && ['document', 'script', 'stylesheet'].includes(type)) {
      resourceResponses.push({ phase: navigationPhase, type, path: url.pathname, status: response.status() })
      if (resourceResponses.length > 50) resourceResponses.shift()
    }
  })
  const redact = (value: string) => value.replace(/([?&]token=)[^&\s]+/gi, '$1[redacted]').slice(0, 500)
  page.on('console', (message) => { if (message.type() === 'error') browserFailures.push(`console: ${redact(message.text())}`) })
  page.on('pageerror', (error) => browserFailures.push(`page: ${redact(error.message)}`))
  const documentResponse = await fetch(origin, { signal: AbortSignal.timeout(15_000) })
  if (!documentResponse.ok) throw new Error(`The isolated console document returned HTTP ${documentResponse.status}`)
  await documentResponse.body?.cancel()
}, 120_000)

afterAll(async () => {
  await browser?.close()
  await vite?.close()
  await stopGateway()
  rmSync(scratch, { recursive: true, force: true })
})

test('identity reducer rejects stale retries and represents read failure separately from empty identity', () => {
  let state = identityReducer(initialIdentity, { type: 'loadStarted', request: 1 })
  state = identityReducer(state, { type: 'loaded', name: 'Ada Lovelace', revision: 0, request: 1 })
  state = identityReducer(state, { type: 'loadStarted', request: 2 })
  expect(identityReducer(state, { type: 'loaded', name: '', revision: 0, request: 1 })).toBe(state)
  state = identityReducer(state, { type: 'loadFailed', error: "Gideon couldn't load your account. Check your connection and try again.", request: 2 })
  expect(state.status).toBe('failed')
  expect(state.name).toBe('Ada Lovelace')
  state = identityReducer(state, { type: 'loadStarted', request: 3 })
  state = identityReducer(state, { type: 'loaded', name: 'Ada Lovelace', revision: 0, request: 3 })
  expect(state.status).toBe('loaded')
  expect(state.name).toBe('Ada Lovelace')
})

describe('live account identity failure and retry', () => {
  test('renders the real shell error during gateway outage, then opens the saved account after restart', async () => {
    const currentPage = page!
    apiResponses.length = 0
    try {
      navigationPhase = 'authenticated bootstrap'
      await currentPage.goto(`${origin}/?token=${encodeURIComponent(gatewayToken)}`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
      navigationPhase = 'dashboard'
      await currentPage.goto(`${origin}/#/dashboard`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    } catch (error) {
      throw new Error(`The isolated console navigation failed (${error instanceof Error ? error.name : 'unknown error'}): ${JSON.stringify({ phase: navigationPhase, pending: [...pendingResources.values()], resources: resourceResponses, browserFailures })}`)
    }
    await currentPage.locator('nav[data-tour="rail"]').waitFor({ timeout: 30_000 })

    await stopGateway()
    requests.length = 0
    apiResponses.length = 0
    navigationPhase = 'gateway outage reload'
    await currentPage.reload({ waitUntil: 'domcontentloaded' })
    await expectHeading(currentPage, "Couldn't load your account")
    await currentPage.getByRole('button', { name: 'Retry', exact: true }).waitFor()
    if (await currentPage.getByRole('heading', { name: /^Welcome to Gideon/ }).count()) throw new Error('Gateway outage incorrectly opened first-run setup')
    await currentPage.screenshot({ path: join(evidenceDir, 'account-read-failed.png'), fullPage: true })
    expect(requests.some((request) => request.method === 'GET' && request.path === '/api/dashboard/config')).toBe(true)
    expect(requests.some((request) => request.method === 'PUT' && request.path === '/api/dashboard/config')).toBe(false)
    const storedWhileDown = JSON.parse(readFileSync(join(home, 'config.json'), 'utf8')) as { dashboard?: { user_name?: string; username?: string } }
    expect(storedWhileDown.dashboard).toMatchObject({ user_name: 'Ada Lovelace', username: 'ada-lovelace' })

    await startGateway()
    navigationPhase = 'gateway restart retry'
    await currentPage.getByRole('button', { name: 'Retry', exact: true }).click()
    await currentPage.locator('nav[data-tour="rail"]').waitFor({ timeout: 30_000 })
    expect(requests.some((request) => request.method === 'GET' && request.path === '/api/dashboard/config')).toBe(true)
  }, 120_000)


})

async function expectHeading(currentPage: Page, name: string): Promise<void> {
  try {
    await currentPage.getByRole('heading', { name, exact: true }).waitFor({ timeout: 30_000 })
  } catch {
    const snapshot = await currentPage.evaluate(() => ({
      path: window.location.pathname,
      ready: document.readyState,
      text: document.body.innerText.slice(0, 3000),
    }))
    await currentPage.screenshot({ path: join(evidenceDir, 'account-read-failed-diagnostic.png'), fullPage: true }).catch(() => {})
    throw new Error(`Expected account read failure heading; browser state: ${JSON.stringify({ document: snapshot, apiResponses, browserFailures })}`)
  }
}
