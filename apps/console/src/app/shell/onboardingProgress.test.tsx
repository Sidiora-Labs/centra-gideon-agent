import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { createServer as createTcpServer } from 'node:net'
import { chmodSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { tmpdir } from 'node:os'
import { chromium, type Browser, type BrowserContext, type Page } from 'playwright'


const saveOnboardingState = vi.fn()
const onboarding = vi.fn()
const setName = vi.fn()

vi.mock('../../shared/data/api', () => ({
  api: {
    saveOnboardingState: (...a: unknown[]) => saveOnboardingState(...a),
    onboarding: () => onboarding(),
    themes: () => new Promise(() => {}),
    gideonConfig: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
  },
}))
vi.mock('./identity', async (importOriginal) => ({
  ...await importOriginal<typeof import('./identity')>(),
  useIdentity: () => ({ setName }),
  firstNameOf: (n: string) => n.split(' ')[0],
  DEFAULT_USER_NAME: 'Operator',
}))
vi.mock('../../shared/ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('../../features/onboarding/ImportStep', () => ({
  ImportStep: ({ onDone, onSkip }: { onDone: (s: string) => void; onSkip: () => void }) => (
    <div>
      <button type="button" onClick={() => onDone('2 imported')}>stub-imported</button>
      <button type="button" onClick={onSkip}>stub-skip-import</button>
    </div>
  ),
}))
vi.mock('../../features/onboarding/EssentialsStep', () => ({
  EssentialsStep: ({ onDone, onSkip }: { onDone: (s: string) => void; onSkip: () => void }) => (
    <div>
      <button type="button" onClick={() => onDone('gpt-5')}>stub-continue</button>
      <button type="button" onClick={onSkip}>stub-skip</button>
    </div>
  ),
}))
vi.mock('../../features/onboarding/TryOneStep', () => ({
  TryOneStep: ({ onDone, onSkip }: { onDone: (s: string) => void; onSkip: () => void }) => (
    <div>
      <button type="button" onClick={() => onDone('1 of 3 tried')}>stub-tried</button>
      <button type="button" onClick={onSkip}>stub-skip-try</button>
    </div>
  ),
}))

import { Onboarding } from './Onboarding'
import { AppearanceProvider } from './appearance'
import { readNavDisclosure } from './navDisclosure'

const ORIGINAL_MATCH_MEDIA = window.matchMedia

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }),
  })
  saveOnboardingState.mockResolvedValue({ ok: true, state: {} })
  onboarding.mockResolvedValue({ needs_model: true, has_model_provider: false, has_chat_binding: false })
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
})

function renderFlow() {
  return render(<AppearanceProvider><Onboarding /></AppearanceProvider>)
}

async function enterName() {
  renderFlow()
  await waitFor(() => expect(onboarding).toHaveBeenCalled())
  fireEvent.change(screen.getByPlaceholderText('Your name'), { target: { value: 'Ada Lovelace' } })
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
}

async function enterNameAndImport() {
  await enterName()
  fireEvent.click(await screen.findByRole('button', { name: 'stub-imported' }))
}


describe('every step transition persists its resume point', () => {
  it('records nothing for the import step, then `essentials` when it is left', async () => {
    await enterName()
    expect(await screen.findByRole('button', { name: 'stub-imported' })).toBeTruthy()
    expect(saveOnboardingState).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'stub-imported' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'essentials' }))
  })

  it('records `essentials` when the import step is SKIPPED too', async () => {
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'essentials' }))
  })

  it('records `first_success` when the essentials step is completed', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'first_success' }))
  })

  it('records `first_success` when the essentials step is SKIPPED too', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'first_success' }))
  })

  it('records `done` and commits the name LAST', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-tried' }))
    fireEvent.click(await screen.findByText(/Start a conversation/))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' }))
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Ada Lovelace', 'ada-lovelace'))
    const steps = saveOnboardingState.mock.calls.map(([p]) => p.step)
    expect(steps).toEqual(['essentials', 'first_success', 'done'])
  })

  it('leaving the first-success step does NOT invent a fourth resume point', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-tried' }))
    const steps = saveOnboardingState.mock.calls.map(([p]) => p.step)
    expect(steps).toEqual(['essentials', 'first_success'])
  })

  it('skipping the first-success step reaches the recap too', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByText(/Start a conversation/)).toBeTruthy()
  })

  it('writes only the `step` key — no lane progress the shell did not observe', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    for (const [patch] of saveOnboardingState.mock.calls) expect(Object.keys(patch)).toEqual(['step'])
  })
})

describe('finishing marks the install as onboarded under THIS version (OU-5 / C4)', () => {
  async function finishFlow() {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    fireEvent.click(await screen.findByText(/Start a conversation/))
  }

  it('writes the starter-rail marker', async () => {
    localStorage.clear()
    localStorage.setItem('nav-disclosure', JSON.stringify({ mode: 'expert', pinned: [] }))
    expect(readNavDisclosure().mode).toBe('expert')
    await finishFlow()
    await waitFor(() => expect(readNavDisclosure().mode).toBe('starter'))
  })

  it('leaves already-earned pins alone', async () => {
    localStorage.setItem('nav-disclosure', JSON.stringify({ mode: 'expert', pinned: ['tools'] }))
    await finishFlow()
    await waitFor(() => expect(readNavDisclosure()).toEqual({ mode: 'starter', pinned: ['tools'] }))
  })
})


describe('re-entering the flow resumes at the persisted step', () => {
  it('lands on the try-one step when the home stopped at first_success', async () => {
    onboarding.mockResolvedValue({
      needs_model: false, has_model_provider: true, has_chat_binding: true,
      step: 'first_success', essentials: { model: 'anthropic-models', search: false, speech: false, channel: null },
      first_success: { knowledge: false, trigger: false, loop: false },
    })
    await enterName()
    expect(await screen.findByRole('button', { name: 'stub-tried' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'stub-continue' })).toBeNull()
  })

  it('does not walk the stored resume point backwards', async () => {
    onboarding.mockResolvedValue({
      needs_model: false, has_model_provider: true, has_chat_binding: true, step: 'first_success',
    })
    await enterName()
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalled())
    expect(saveOnboardingState.mock.calls.map(([p]) => p.step)).toEqual(['first_success'])
  })

  it('restates what the earlier visit set up, checked against live readiness', async () => {
    onboarding.mockResolvedValue({
      needs_model: false, has_model_provider: true, has_chat_binding: true,
      step: 'first_success', essentials: { model: 'anthropic-models', search: false, speech: false, channel: null },
      first_success: { knowledge: true, trigger: false, loop: false },
    })
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByText('anthropic-models')).toBeTruthy()
    expect(screen.getByText('Chat model: anthropic-models')).toBeTruthy()
    expect(screen.getByText(/1 of 3 tried/, { selector: 'p' })).toBeTruthy()
  })

  it('does not promise a model the home no longer resolves', async () => {
    onboarding.mockResolvedValue({
      needs_model: true, has_model_provider: false, has_chat_binding: false,
      step: 'first_success', essentials: { model: 'anthropic-models', search: false, speech: false, channel: null },
    })
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByText(/Chat model — set up later in Settings/)).toBeTruthy()
    expect(screen.queryByText(/Chat model: anthropic-models/)).toBeNull()
  })

  it('starts a completed home over instead of dropping it on the recap', async () => {
    onboarding.mockResolvedValue({
      needs_model: true, has_model_provider: false, has_chat_binding: false, step: 'done',
    })
    await enterName()
    expect(await screen.findByRole('button', { name: 'stub-imported' })).toBeTruthy()
  })
})

describe('skip at any step lands in a working dashboard', () => {
  it('names the default it will use, rather than renaming you silently', async () => {
    renderFlow()
    await waitFor(() => expect(onboarding).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: /Skip setup — start as Operator/ })).toBeTruthy()
  })

  it('skips from a MIDDLE step, keeping the name that was typed', async () => {
    await enterNameAndImport()
    expect(await screen.findByRole('button', { name: 'stub-continue' })).toBeTruthy()
    fireEvent.click(screen.getByText(/Skip setup and start a conversation/))
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Ada Lovelace', 'ada-lovelace'))
    expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' })
  })

  it('offers no skip on the last step — the conversation action is the door', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByText(/Start a conversation/)).toBeTruthy()
    expect(screen.queryByText(/^Skip setup/)).toBeNull()
  })
})

describe('a failed progress write costs the user nothing', () => {
  it('still advances when the resume-point POST rejects', async () => {
    saveOnboardingState.mockRejectedValue(new Error('gateway down'))
    await enterNameAndImport()
    expect(await screen.findByRole('button', { name: 'stub-continue' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-tried' }))
    expect(await screen.findByText(/Start a conversation/)).toBeTruthy()
  })
})

it('records the tour request before the completed flow releases the identity gate', async () => {
  const { consumeProductTourRequest } = await import('../../features/onboarding/tourLaunch')
  consumeProductTourRequest()
  await enterNameAndImport()
  fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
  fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
  fireEvent.click(await screen.findByRole('button', { name: /Take the quick tour/ }))
  expect(consumeProductTourRequest()).toBe(true)
  expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' })
  await waitFor(() => expect(setName).toHaveBeenCalledWith('Ada Lovelace', 'ada-lovelace'))
})

it('uses the real gateway to keep a stale second-client Skip from replacing a saved identity', async () => {
  const sourceDir = dirname(fileURLToPath(import.meta.url))
  const repoRoot = resolve(sourceDir, '../../../../..')
  const consoleRoot = resolve(repoRoot, 'apps/console')
  const runtimeRoot = process.env.GIDEON_TEST_RUNTIME_ROOT || resolve(repoRoot, 'runtime')
  const gatewayPython = process.env.GIDEON_TEST_PYTHON || resolve(repoRoot, '.venv/bin/python')
  const scratch = mkdtempSync(join(tmpdir(), 'gideon-onboarding-atomic-'))
  const home = join(scratch, 'home')
  const workspace = join(home, 'workspace')
  const evidenceDir = mkdtempSync(join(tmpdir(), 'gideon-onboarding-atomic-evidence-'))
  chmodSync(evidenceDir, 0o700)
  let gatewayPort = 0
  let gateway: ChildProcess | undefined
  let gatewayStderr = ''
  let gatewayToken = ''
  let viteProcess: ChildProcess | undefined
  let viteStderr = ''
  let vitePort = 0
  let browser: Browser | undefined
  let clientA: BrowserContext | undefined
  let clientB: BrowserContext | undefined
  let pageA: Page | undefined
  let pageB: Page | undefined
  let origin = ''
  let keepEvidence = false
  const observedApiRequests: Array<{ method: string; path: string; status?: number }> = []
  const browserFailures: string[] = []
  const clientAWrites: Array<{ method: string; body?: unknown }> = []
  const clientBWrites: Array<{ method: string; body?: unknown }> = []

  async function unusedPort(): Promise<number> {
    const probe = createTcpServer()
    await new Promise<void>((resolveListen, reject) => probe.once('error', reject).listen(0, '127.0.0.1', resolveListen))
    const address = probe.address()
    if (!address || typeof address === 'string') throw new Error('Could not reserve an isolated gateway port')
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
        rejectReady(new Error(`Isolated Gideon gateway exited before readiness (code ${code}, signal ${signal}). ${gatewayStderr.slice(-2000)}`))
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
  }

  async function startConsole(): Promise<void> {
    vitePort = await unusedPort()
    viteProcess = spawn('npm', ['run', 'dev', '--', '--host', '127.0.0.1', '--port', String(vitePort), '--strictPort'], {
      cwd: consoleRoot,
      env: { ...process.env, GIDEON_PORT: String(gatewayPort) },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    viteStderr = ''
    viteProcess.stderr!.on('data', (chunk: Buffer) => { viteStderr += chunk.toString() })
    viteProcess.stdout!.on('data', (chunk: Buffer) => { viteStderr += chunk.toString() })
    origin = `http://127.0.0.1:${vitePort}`
    const deadline = Date.now() + 90_000
    while (Date.now() < deadline) {
      if (viteProcess.exitCode !== null) throw new Error(`The isolated Gideon console exited before readiness. ${viteStderr.slice(-2000)}`)
      try {
        const response = await fetch(origin, { signal: AbortSignal.timeout(2_000) })
        if (response.ok) {
          await response.body?.cancel()
          return
        }
        await response.body?.cancel()
      } catch {}
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 200))
    }
    throw new Error(`The isolated Gideon console did not become ready. ${viteStderr.slice(-2000)}`)
  }

  async function stopConsole(): Promise<void> {
    const child = viteProcess
    if (!child || child.exitCode !== null) return
    await new Promise<void>((resolveStop) => {
      const timeout = setTimeout(() => { child.kill('SIGKILL') }, 10_000)
      child.once('exit', () => { clearTimeout(timeout); resolveStop() })
      child.kill('SIGTERM')
    })
    viteProcess = undefined
  }

  function watchConfigWrites(page: Page, writes: Array<{ method: string; body?: unknown }>): void {
    page.on('request', (request) => {
      const url = new URL(request.url())
      if (url.pathname !== '/api/dashboard/config' || request.method() === 'GET') return
      let body: unknown
      try { body = request.postDataJSON() } catch {}
      writes.push({ method: request.method(), body })
    })
  }

  try {
    if (!existsSync(gatewayPython)) throw new Error('The isolated Gideon runtime Python executable is unavailable')
    mkdirSync(workspace, { recursive: true })
    writeFileSync(join(home, 'config.json'), JSON.stringify({ dashboard: {} }))
    gatewayPort = await unusedPort()
    await startGateway()
    await startConsole()
    const entryResponse = await fetch(`${origin}/src/app/bootstrap/main.tsx`, { signal: AbortSignal.timeout(30_000) })
    if (!entryResponse.ok) throw new Error(`The isolated console entry returned HTTP ${entryResponse.status}`)
    await entryResponse.arrayBuffer()
    browser = await chromium.launch({
      ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {}),
      headless: true,
      args: ['--no-sandbox'],
    })
    clientA = await browser.newContext({ viewport: { width: 1280, height: 900 } })
    clientB = await browser.newContext({ viewport: { width: 1280, height: 900 } })
    const currentPageA = await clientA.newPage()
    const currentPageB = await clientB.newPage()
    pageA = currentPageA
    pageB = currentPageB
    watchConfigWrites(currentPageA, clientAWrites)
    watchConfigWrites(currentPageB, clientBWrites)
    for (const page of [currentPageA, currentPageB]) {
      page.on('request', (request) => {
        const url = new URL(request.url())
        if (url.pathname.startsWith('/api/')) observedApiRequests.push({ method: request.method(), path: url.pathname })
      })
      page.on('response', (response) => {
        const url = new URL(response.url())
        if (url.pathname.startsWith('/api/')) observedApiRequests.push({ method: response.request().method(), path: url.pathname, status: response.status() })
      })
      page.on('pageerror', (error) => browserFailures.push(error.message.slice(0, 500)))
      page.on('console', (message) => { if (message.type() === 'error') browserFailures.push(message.text().slice(0, 500)) })
    }

    await currentPageA.goto(`${origin}/?token=${encodeURIComponent(gatewayToken)}`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    await currentPageA.goto(`${origin}/#/dashboard`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    await currentPageA.getByPlaceholder('Your name').waitFor({ timeout: 30_000 })
    await stopGateway()
    await currentPageA.reload({ waitUntil: 'domcontentloaded', timeout: 30_000 })
    await currentPageA.getByRole('heading', { name: "Couldn't load your account", exact: true }).waitFor({ timeout: 30_000 })
    await currentPageA.getByRole('button', { name: 'Retry', exact: true }).waitFor()
    expect(clientAWrites).toEqual([])

    await startGateway()
    await currentPageA.getByRole('button', { name: 'Retry', exact: true }).click()
    await currentPageA.getByPlaceholder('Your name').waitFor({ timeout: 30_000 })
    await currentPageB.goto(`${origin}/?token=${encodeURIComponent(gatewayToken)}`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    await currentPageB.goto(`${origin}/#/dashboard`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    await currentPageB.getByPlaceholder('Your name').waitFor({ timeout: 30_000 })

    const nameInput = currentPageA.getByPlaceholder('Your name')
    await nameInput.fill('Ada Lovelace')
    await nameInput.press('Enter')
    await currentPageA.getByText('Skip setup and start a conversation').waitFor({ timeout: 30_000 })
    await currentPageA.getByText('Skip setup and start a conversation').click()
    await currentPageA.locator('nav[data-tour="rail"]').waitFor({ timeout: 30_000 })
    const savedBeforeSkip = await currentPageB.evaluate(async () => {
      const response = await fetch('/api/dashboard/config', { headers: { 'X-Session-Key': 'dashboard:ui' } })
      if (!response.ok) throw new Error(`Config read returned HTTP ${response.status}`)
      return response.json() as Promise<{ user_name?: string; username?: string }>
    })
    expect(savedBeforeSkip).toMatchObject({ user_name: 'Ada Lovelace', username: 'ada-lovelace' })
    await currentPageB.getByText('Skip setup — start as Operator, rename yourself in Settings').waitFor()
    await currentPageB.getByText(/^Skip setup/).click()
    await currentPageB.locator('nav[data-tour="rail"]').waitFor({ timeout: 30_000 })

    const skipWrite = clientBWrites.find((write) => write.method === 'PUT' && write.body && typeof write.body === 'object' && (write.body as { operation?: unknown }).operation === 'keep_or_default_name')
    expect(skipWrite).toBeTruthy()
    const saved = await currentPageB.evaluate(async () => {
      const response = await fetch('/api/dashboard/config', { headers: { 'X-Session-Key': 'dashboard:ui' } })
      if (!response.ok) throw new Error(`Config read returned HTTP ${response.status}`)
      return response.json() as Promise<{ user_name?: string; username?: string }>
    })
    expect(saved).toMatchObject({ user_name: 'Ada Lovelace', username: 'ada-lovelace' })
  } catch (error) {
    keepEvidence = true
    const screenshots: Array<Promise<void>> = []
    if (pageA) screenshots.push(pageA.screenshot({ path: join(evidenceDir, 'client-a.png'), fullPage: true }).then(() => undefined, () => undefined))
    if (pageB) screenshots.push(pageB.screenshot({ path: join(evidenceDir, 'client-b.png'), fullPage: true }).then(() => undefined, () => undefined))
    await Promise.all(screenshots)
    const redact = (value: string) => value
      .replace(/([?&]token=)[^&\s]+/gi, '$1[redacted]')
      .replace(/(authorization\s*[:=]\s*)[^\s,]+/gi, '$1[redacted]')
      .replace(/\bBearer\s+[^\s,]+/gi, 'Bearer [redacted]')
    writeFileSync(join(evidenceDir, 'failure.json'), JSON.stringify({
      error: redact(error instanceof Error ? error.stack ?? error.message : String(error)),
      gatewayStderr: redact(gatewayStderr.slice(-5000)),
      consoleOutput: redact(viteStderr.slice(-5000)),
      apiRequests: observedApiRequests,
      configWrites: { clientA: clientAWrites, clientB: clientBWrites },
      browserFailures: browserFailures.map(redact),
    }, null, 2))
    throw new Error(`${error instanceof Error ? error.message : String(error)}; private browser evidence: ${evidenceDir}`)
  } finally {
    await clientA?.close()
    await clientB?.close()
    await browser?.close()
    await stopConsole()
    await stopGateway()
    rmSync(scratch, { recursive: true, force: true })
    if (!keepEvidence) rmSync(evidenceDir, { recursive: true, force: true })
  }
}, 180_000)
