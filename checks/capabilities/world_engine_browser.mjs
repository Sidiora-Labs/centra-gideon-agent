import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { chromium } from 'playwright'


const root = resolve(process.cwd())
const mode = process.env.WORLD_BROWSER_MODE || 'source'
assert.equal(mode, 'source', 'The source harness only accepts WORLD_BROWSER_MODE=source')
const product = { name: mode, root, hosted: false }
const evidence = await mkdtemp(resolve(tmpdir(), 'gideon-world-engine-browser-'))
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || '/snap/bin/chromium',
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})
const results = []


async function startServer(product) {
  const child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', ['checks/capabilities/world_engine_browser_server.py'], {
    cwd: product.root,
    env: { ...process.env, PYTHONPATH: resolve(product.root, 'runtime'), GIDEON_HOME: resolve(evidence, product.name + '-home'),
      WORLD_BROWSER_MODE: mode, WORLD_ENGINE_DEPS: process.env.WORLD_ENGINE_DEPS || '/tmp/gideon-world-engine-deps' },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let stderr = ''
  child.stderr.on('data', chunk => { stderr += chunk.toString() })
  const ready = await new Promise((accept, reject) => {
    const timeout = setTimeout(() => reject(new Error(`${product.name} server timeout: ${stderr}`)), 120000)
    createInterface({ input: child.stdout }).on('line', line => {
      try { const value = JSON.parse(line); if (value.port) { clearTimeout(timeout); accept(value) } } catch {}
    })
    child.once('exit', code => { clearTimeout(timeout); reject(new Error(`${product.name} server exited ${code}: ${stderr}`)) })
  })
  return { child, stderr: () => stderr, base: `http://127.0.0.1:${ready.port}` }
}


async function qualify(product) {
  const server = await startServer(product)
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const consoleErrors = [], pageErrors = [], failedRequests = []
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  page.on('pageerror', error => pageErrors.push(error.message))
  page.on('requestfailed', request => failedRequests.push({ url: request.url(), error: request.failure()?.errorText || 'failed' }))
  try {
    const engineURL = server.base + '/api/capabilities/experience/world-engine'
    const startedResponse = await page.request.post(engineURL + '/start', { data: {} })
    assert.equal(startedResponse.status(), 200, await startedResponse.text())
    let started = await startedResponse.json()
    const startDeadline = Date.now() + 120000
    while (started.state === 'starting' && Date.now() < startDeadline) {
      await page.waitForTimeout(500)
      const statusResponse = await page.request.get(engineURL)
      assert.equal(statusResponse.status(), 200, await statusResponse.text())
      started = await statusResponse.json()
    }
    assert.equal(started.state, 'running')
    assert.ok(started.version?.sha)

    await page.goto(server.base + '/world-browser?probe=1')
    const gpu = await page.evaluate(async () => {
      if (!navigator.gpu) return { api: false, adapter: false }
      const adapter = await navigator.gpu.requestAdapter().catch(() => null)
      return { api: true, adapter: Boolean(adapter) }
    })
    const backend = gpu.adapter ? 'webgpu' : 'webgl'
    await page.goto(server.base + '/world-browser?backend=' + backend, { waitUntil: 'domcontentloaded', timeout: 120000 })
    await page.locator('iframe#alice').waitFor({ state: 'attached', timeout: 30000 })
    await page.locator('iframe#bob').waitFor({ state: 'attached', timeout: 30000 })
    const deadline = Date.now() + 30000
    let alice, bob
    while ((!alice || !bob) && Date.now() < deadline) {
      alice = page.frame({ name: 'alice' }); bob = page.frame({ name: 'bob' })
      if (!alice || !bob) await page.waitForTimeout(100)
    }
    assert.ok(alice && bob, 'world iframes were not created')
    for (const frame of [alice, bob]) {
      await frame.waitForFunction(() => globalThis.__ewEngineUp === true && globalThis.EW?.net?.joined === true, null, { timeout: 120000 })
      await frame.locator('canvas').waitFor({ state: 'visible', timeout: 30000 })
    }
    const renderer = await alice.evaluate(() => ({
      backend: globalThis.EW.renderer.backend?.isWebGLBackend ? 'webgl' : 'webgpu',
      canvas: { width: globalThis.EW.renderer.domElement.width, height: globalThis.EW.renderer.domElement.height },
      joined: globalThis.EW.net.joined,
    }))
    assert.equal(renderer.backend, backend)
    assert.ok(renderer.canvas.width > 0 && renderer.canvas.height > 0)

    await alice.waitForFunction(() => globalThis.EW.remotes.has('bob'), null, { timeout: 30000 })
    await bob.waitForFunction(() => globalThis.EW.remotes.has('alice'), null, { timeout: 30000 })
    const presence = {
      alice: await alice.evaluate(() => [...globalThis.EW.remotes.keys()]),
      bob: await bob.evaluate(() => [...globalThis.EW.remotes.keys()]),
    }
    const before = await alice.evaluate(() => globalThis.EW.myState.pos.toArray())
    await alice.locator('canvas').click({ position: { x: 300, y: 250 } })
    await page.keyboard.down('w')
    await page.waitForTimeout(900)
    await page.keyboard.up('w')
    const after = await alice.evaluate(() => globalThis.EW.myState.pos.toArray())
    assert.notDeepEqual(after, before, 'keyboard scene input did not move the local body')

    const marker = `browser-save-${product.name}-${Date.now()}`
    await alice.evaluate(text => globalThis.EW.sendVerb('say', { text }), marker)
    await alice.locator('#chatlog').getByText(marker, { exact: false }).waitFor({ timeout: 30000 })
    await alice.evaluate(() => location.reload())
    await alice.waitForFunction(() => globalThis.__ewEngineUp === true && globalThis.EW?.net?.joined === true, null, { timeout: 120000 })
    await alice.locator('#chatlog').getByText(marker, { exact: false }).waitFor({ timeout: 30000 })
    await page.screenshot({ path: resolve(evidence, product.name + '-world.png'), fullPage: true })
    return { product: product.name, status: 'passed', signed_edge: product.hosted, started, gpu,
      renderer, presence, input: { before, after }, durable_marker: marker,
      screenshot: product.name + '-world.png', page_errors: pageErrors,
      console_errors: consoleErrors, failed_requests: failedRequests }
  } finally {
    await page.close()
    server.child.kill('SIGTERM')
    await writeFile(resolve(evidence, product.name + '-runtime.log'), server.stderr())
  }
}


try {
  results.push(await qualify(product))
  const record = { status: 'passed', browser: await browser.version(), results,
    reused_runtime_evidence: process.env.WORLD_BROWSER_RUNTIME_EVIDENCE || null,
    hardware_boundary: results.every(result => result.gpu.adapter) ? null : 'Browser host exposed no WebGPU adapter; genuine engine WebGL fallback was exercised for affected products.',
  }
  await writeFile(resolve(evidence, 'evidence.json'), JSON.stringify(record, null, 2))
  process.stdout.write(JSON.stringify({ status: 'passed', evidence }) + '\n')
} catch (error) {
  await writeFile(resolve(evidence, 'failure.json'), JSON.stringify({ status: 'failed', error: String(error), results }, null, 2))
  process.stderr.write(JSON.stringify({ status: 'failed', evidence, error: String(error) }) + '\n')
  process.exitCode = 1
} finally {
  await browser.close()
}
