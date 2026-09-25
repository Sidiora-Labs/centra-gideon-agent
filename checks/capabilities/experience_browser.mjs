import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { dirname } from 'node:path'
import { createInterface } from 'node:readline'
import { createRequire } from 'node:module'
import { chromium } from 'playwright'

const root = process.cwd()
const consoleRequire = createRequire(new URL('../../apps/console/package.json', import.meta.url))
const { createServer } = await import(consoleRequire.resolve('vite'))
const { default: react } = await import(consoleRequire.resolve('@vitejs/plugin-react'))
const { default: tailwindcss } = await import(consoleRequire.resolve('@tailwindcss/vite'))
const frontendPort = 39000 + Math.floor(Math.random() * 10000)
const phase = process.env.EXPERIENCE_BROWSER_PHASE || 'all'
assert.ok(['all', 'avatar'].includes(phase), `Unknown EXPERIENCE_BROWSER_PHASE: ${phase}`)
const evidence = await mkdtemp(resolve(tmpdir(), 'gideon-experience-browser-'))
const runtime = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', ['checks/capabilities/experience_server.py'], {
  cwd: root,
  env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: resolve(evidence, 'home'), GIDEON_SKIP_APP_BACKENDS: '1', GIDEON_AUTH_MODE: 'local_token', GIDEON_BROWSER_ORIGIN: `http://127.0.0.1:${frontendPort}` },
  stdio: ['ignore', 'pipe', 'pipe'],
})
let backendLog = ''
runtime.stderr.on('data', chunk => { backendLog += chunk.toString() })
let server, browser, page

try {
  const ready = await new Promise((accept, reject) => {
    const timeout = setTimeout(() => reject(new Error('Runtime did not start: ' + backendLog)), 60000)
    createInterface({ input: runtime.stdout }).on('line', line => {
      try { const value = JSON.parse(line); if (value.port) { clearTimeout(timeout); accept(value) } } catch {}
    })
    runtime.once('exit', code => { clearTimeout(timeout); reject(new Error(`Runtime exited ${code}: ${backendLog}`)) })
  })
  let installedThree = ''
  try { installedThree = dirname(dirname(consoleRequire.resolve('three'))) } catch {}
  const threeRoot = [installedThree, process.env.THREE_ROOT, '/tmp/gideon-music-three-deps/node_modules/three']
    .find(path => path && existsSync(path + '/build/three.module.js')) || ''
  const aliases = existsSync(threeRoot) ? [
    { find: /^three\/addons\/(.*)$/, replacement: threeRoot + '/examples/jsm/$1' },
    { find: /^three\/(.*)$/, replacement: threeRoot + '/$1' },
    { find: 'three', replacement: threeRoot + '/build/three.module.js' },
  ] : []
  server = await createServer({
    configFile: false,
    resolve: { alias: aliases },
    root: resolve(root, 'apps/console'),
    plugins: [react(), tailwindcss(), {
      name: 'experience-browser-entry',
      configureServer(dev) {
        dev.middlewares.use('/capability-journey', async (_request, response) => {
          response.setHeader('Content-Type', 'text/html')
          response.end(await dev.transformIndexHtml('/capability-journey', `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@fs/${resolve(root, 'checks/capabilities/full_entry.tsx')}"></script></body></html>`))
        })
      },
    }],
    server: { host: '127.0.0.1', port: frontendPort, strictPort: true, fs: { allow: [root] }, proxy: { '/api': { target: `http://127.0.0.1:${ready.port}`, changeOrigin: true } } },
  })
  await server.listen()
  const base = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || '/opt/chromium/chrome-linux64/chrome', headless: true, args: ['--no-sandbox'] })
  page = await browser.newPage({ viewport: { width: 1440, height: 960 } })
  const pageErrors = []
  page.on('pageerror', error => pageErrors.push(error.message))
  await page.goto(base + '/api/capabilities/identity/stories?token=' + encodeURIComponent(ready.token))
  const configured = await page.request.put(base + '/api/dashboard/config', { data: { user_name: 'Experience Browser Operator' }, headers: { Origin: base } })
  assert.equal(configured.status(), 200, await configured.text())
  await page.goto(base + '/capability-journey#/capabilities/experience', { waitUntil: 'domcontentloaded', timeout: 120000 })
  await page.getByRole('heading', { name: 'Interactive stories', exact: true }).waitFor()

  let desktopGeometry = null, mobileGeometry = null, fullscreen = null
  if (phase !== 'avatar') {
  await page.getByRole('button', { name: 'Open ambient display', exact: true }).click()
  const ambient = page.getByRole('main', { name: 'Ambient display' })
  await ambient.waitFor()
  await page.getByText('Last observed:', { exact: false }).waitFor()
  desktopGeometry = await ambient.evaluate(element => {
    const box = element.getBoundingClientRect()
    return { x: box.x, y: box.y, width: box.width, height: box.height, viewport: { width: innerWidth, height: innerHeight }, overflow: document.documentElement.scrollWidth > innerWidth }
  })
  assert.equal(desktopGeometry.x, 0)
  assert.equal(desktopGeometry.y, 0)
  assert.equal(desktopGeometry.width, desktopGeometry.viewport.width)
  assert.ok(desktopGeometry.height >= desktopGeometry.viewport.height)
  assert.equal(desktopGeometry.overflow, false)
  await page.screenshot({ path: resolve(evidence, 'ambient-desktop.png'), fullPage: true })

  await page.getByRole('button', { name: 'Enter fullscreen', exact: true }).click()
  await page.getByRole('button', { name: 'Fullscreen active', exact: true }).waitFor()
  fullscreen = await page.evaluate(() => ({ supported: typeof document.documentElement.requestFullscreen === 'function', active: document.fullscreenElement?.getAttribute('aria-label') || null }))
  assert.deepEqual(fullscreen, { supported: true, active: 'Ambient display' })
  await page.screenshot({ path: resolve(evidence, 'ambient-fullscreen.png'), fullPage: true })
  await page.evaluate(() => document.exitFullscreen())
  await page.getByRole('button', { name: 'Enter fullscreen', exact: true }).waitFor()

  await page.setViewportSize({ width: 390, height: 844 })
  mobileGeometry = await ambient.evaluate(element => {
    const box = element.getBoundingClientRect()
    return { x: box.x, y: box.y, width: box.width, height: box.height, viewport: { width: innerWidth, height: innerHeight }, overflow: document.documentElement.scrollWidth > innerWidth }
  })
  assert.equal(mobileGeometry.x, 0)
  assert.equal(mobileGeometry.y, 0)
  assert.equal(mobileGeometry.width, mobileGeometry.viewport.width)
  assert.ok(mobileGeometry.height >= mobileGeometry.viewport.height)
  assert.equal(mobileGeometry.overflow, false)
  await page.screenshot({ path: resolve(evidence, 'ambient-mobile.png'), fullPage: true })
  await page.getByRole('button', { name: 'Exit ambient display', exact: true }).click()
  await page.getByRole('heading', { name: 'Animated avatar', exact: true }).waitFor()
  }
  await page.setViewportSize({ width: 1280, height: 960 })

  const avatarPanel = page.getByRole('heading', { name: 'Animated avatar', exact: true }).locator('..')
  await avatarPanel.getByRole('button', { name: /^Install bundled (?:robot|avatar)$/ }).click()
  const avatarSelect = avatarPanel.locator('select').first()
  await avatarSelect.locator('option').nth(1).waitFor({ state: 'attached' })
  const avatarID = await avatarSelect.locator('option').nth(1).getAttribute('value')
  assert.ok(avatarID)
  await avatarSelect.selectOption(avatarID)
  const canvas = avatarPanel.locator('canvas')
  await canvas.waitFor({ timeout: 30000 })
  await page.getByText(/^Avatar state: idle$/i).waitFor()
  const renderer = await canvas.evaluate(async element => {
    const gl = element.getContext('webgl2') || element.getContext('webgl')
    if (!gl) return { available: false }
    await new Promise(resolveFrame => requestAnimationFrame(() => requestAnimationFrame(resolveFrame)))
    const pixels = new Uint8Array(element.width * element.height * 4)
    gl.readPixels(0, 0, element.width, element.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels)
    let coloredPixels = 0
    for (let index = 0; index < pixels.length; index += 4) if (pixels[index] || pixels[index + 1] || pixels[index + 2]) coloredPixels++
    const debug = gl.getExtension('WEBGL_debug_renderer_info')
    return {
      available: true,
      context: gl instanceof WebGL2RenderingContext ? 'webgl2' : 'webgl',
      vendor: gl.getParameter(debug ? debug.UNMASKED_VENDOR_WEBGL : gl.VENDOR),
      renderer: gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER),
      version: gl.getParameter(gl.VERSION),
      width: element.width,
      height: element.height,
      colored_pixels: coloredPixels,
    }
  })
  assert.equal(renderer.available, true, 'The real browser did not provide a WebGL context')
  assert.ok(renderer.width > 0 && renderer.height > 0)
  assert.ok(renderer.colored_pixels > 0, 'The authored avatar canvas contained no rendered color pixels')
  const avatarsResponse = await page.request.get(base + '/api/capabilities/experience/avatars')
  assert.equal(avatarsResponse.status(), 200)
  const avatar = (await avatarsResponse.json()).avatars.find(row => row.id === avatarID)
  assert.ok(avatar && avatar.availability === 'ready')
  const rawModel = await page.request.get(`${base}/api/capabilities/experience/avatar-assets/${encodeURIComponent(avatar.artifact_slug)}/${avatar.artifact_version}/raw`)
  assert.equal(rawModel.status(), 200)
  assert.ok((await rawModel.body()).byteLength > 1000)
  await page.screenshot({ path: resolve(evidence, 'avatar-idle.png'), fullPage: true })

  const loopResponse = await page.request.post(base + '/api/loops', { data: { kind: 'goal', task: 'Observe the real authored avatar activity transition', attended: true, autopilot: false, max_cycles: 1 }, headers: { Origin: base } })
  assert.equal(loopResponse.status(), 201, await loopResponse.text())
  const loop = await loopResponse.json()
  const activitySelect = avatarPanel.locator('select').nth(1)
  await page.reload()
  await page.waitForFunction(value => Array.from(document.querySelectorAll('select option')).some(option => option.value === value), `loop:${loop.id}`, { timeout: 15000 })
  await activitySelect.selectOption(`loop:${loop.id}`)
  await page.getByText(/^Avatar state: idle$/i).waitFor()
  const startResponse = await page.request.patch(`${base}/api/loops/${loop.id}`, { data: { action: 'start' }, headers: { Origin: base } })
  assert.equal(startResponse.status(), 200, await startResponse.text())
  const started = await startResponse.json()
  await page.reload()
  const liveStatus = page.getByRole('status').filter({ hasText: 'Avatar state:' })
  await liveStatus.waitFor()
  const transitionText = await liveStatus.textContent()
  assert.match(transitionText || '', /^Avatar state: working$/i)
  await page.screenshot({ path: resolve(evidence, 'avatar-runtime-transition.png'), fullPage: true })
  const pauseResponse = await page.request.patch(`${base}/api/loops/${loop.id}`, { data: { action: 'pause' }, headers: { Origin: base } })
  assert.equal(pauseResponse.status(), 200, await pauseResponse.text())
  const paused = await pauseResponse.json()
  await page.reload()
  await page.getByText(/^Avatar state: idle$/i).waitFor()
  await page.screenshot({ path: resolve(evidence, 'avatar-runtime-paused.png'), fullPage: true })
  assert.deepEqual(pageErrors, [])

  const result = {
    status: 'passed',
    phase,
    browser: await browser.version(),
    ...(phase === 'all' ? { ambient: { fullscreen, desktop_geometry: desktopGeometry, mobile_geometry: mobileGeometry } } : {}),
    avatar: {
      id: avatarID,
      artifact: `${avatar.artifact_slug}:${avatar.artifact_version}`,
      clips: avatar.clips,
      renderer,
      runtime_transition: { states: ['idle', 'working', 'idle'], loop_id: loop.id, started_status: started.status, paused_status: paused.status },
    },
    screenshots: phase === 'avatar' ? ['avatar-idle.png', 'avatar-runtime-transition.png', 'avatar-runtime-paused.png'] : ['ambient-desktop.png', 'ambient-fullscreen.png', 'ambient-mobile.png', 'avatar-idle.png', 'avatar-runtime-transition.png', 'avatar-runtime-paused.png'],
    page_errors: pageErrors,
    external_limits: ['Audible PCM output and physical audio device were not exercised.', 'Renderer metadata records the browser-provided WebGL implementation; it does not claim discrete GPU hardware.'],
  }
  await writeFile(resolve(evidence, 'evidence.json'), JSON.stringify(result, null, 2))
  process.stdout.write(JSON.stringify({ status: 'passed', evidence }) + '\n')
} catch (error) {
  if (page) {
    await page.screenshot({ path: resolve(evidence, 'failure.png'), fullPage: true }).catch(() => {})
    await writeFile(resolve(evidence, 'page.txt'), await page.locator('body').innerText().catch(() => ''))
  }
  await writeFile(resolve(evidence, 'failure.json'), JSON.stringify({ error: String(error), url: page?.url(), backendLog }, null, 2))
  process.stderr.write(JSON.stringify({ status: 'failed', evidence, error: String(error) }) + '\n')
  process.exitCode = 1
} finally {
  if (browser) await browser.close()
  if (server) await server.close()
  runtime.kill('SIGTERM')
  await writeFile(resolve(evidence, 'runtime.log'), backendLog)
}
