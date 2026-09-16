#!/usr/bin/env node

import { chromium } from 'playwright'
import { existsSync, readdirSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

function parseArgs(argv) {
  const out = {
    url: process.env.GIDEON_MOTION_URL || 'http://127.0.0.1:10473',
    bounciness: [1, 0],
    surfaces: null,
    cpuThrottle: Number(process.env.GIDEON_MOTION_CPU_THROTTLE || 1),
    injectStall: 0,
    headed: false,
    chromium: process.env.GIDEON_MOTION_CHROMIUM || '',
    windowMs: 3000,
    viewport: { width: 1440, height: 900 },
  }
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i]
    const next = () => argv[++i]
    if (a === '--url') out.url = next()
    else if (a === '--bounciness') out.bounciness = next().split(',').map(Number)
    else if (a === '--surfaces') out.surfaces = next().split(',')
    else if (a === '--cpu-throttle') out.cpuThrottle = Number(next())
    else if (a === '--inject-stall') out.injectStall = Number(next())
    else if (a === '--window-ms') out.windowMs = Number(next())
    else if (a === '--viewport') {
      const [w, h] = next().split('x').map(Number)
      out.viewport = { width: w, height: h }
    }
    else if (a === '--headed') out.headed = true
    else if (a === '--chromium') out.chromium = next()
    else if (a === '--help' || a === '-h') { usage(); process.exit(0) }
    else { console.error(`unknown flag: ${a}`); usage(); process.exit(2) }
  }
  return out
}

function usage() {
  console.error(`motion_frame_budget — frame-time distributions per surface per bounciness

  --url <base>          gateway serving the built SPA (default $GIDEON_MOTION_URL or :10473)
  --bounciness <list>   comma list of --bounciness values to measure (default "1,0")
  --surfaces <list>     comma list of surface ids (default all: ${SURFACES.map((s) => s.id).join(',')})
  --cpu-throttle <n>    CDP CPU throttling rate, 1 = none (default 1)
  --inject-stall <ms>   FALSIFICATION: block the main thread this long inside the window
  --window-ms <ms>      length of the idle control window (default 3000)
  --viewport <WxH>      viewport (default 1440x900) — it decides which lists overflow
  --headed              run the full chromium headed instead of the headless shell
  --chromium <path>     explicit browser executable`)
}

function resolveBrowser({ headed, chromium: explicit }) {
  if (explicit) return { executablePath: explicit, label: explicit }
  const cache = path.join(os.homedir(), 'Library', 'Caches', 'ms-playwright')
  const arch = process.arch === 'arm64' ? 'arm64' : 'x64'
  const candidates = []
  if (existsSync(cache)) {
    for (const dir of readdirSync(cache)) {
      if (headed && /^chromium-\d+$/.test(dir)) {
        candidates.push(path.join(cache, dir, `chrome-mac-${arch}`,
          'Google Chrome for Testing.app', 'Contents', 'MacOS', 'Google Chrome for Testing'))
      }
      if (!headed && /^chromium_headless_shell-\d+$/.test(dir)) {
        candidates.push(path.join(cache, dir, `chrome-headless-shell-mac-${arch}`, 'chrome-headless-shell'))
      }
    }
  }
  candidates.sort().reverse()
  const hit = candidates.find((p) => existsSync(p))
  if (hit) return { executablePath: hit, label: hit }
  return { executablePath: undefined, label: 'playwright default' }
}


const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function navigateIn(page, surface) {
  await page.evaluate((r) => { location.hash = r.slice(1) }, surface.route)
  await sleep(1400)
  return true
}

const PALETTE = '[role="listbox"][aria-label="Commands"]'

async function paletteOpen(page) {
  await page.keyboard.press('ControlOrMeta+k')
  await page.locator(PALETTE).waitFor({ state: 'visible', timeout: 4000 }).catch(() => {})
  await sleep(500)
  const n = await page.locator(`${PALETTE} [role="option"]`).count()
  if (!n) return { skip: `${PALETTE} did not appear on ControlOrMeta+K — the palette did not open` }
  return true
}

async function paletteScrollAndClose(page) {
  const box = await page.evaluate((sel) => {
    const lb = document.querySelector(sel)
    if (!lb) return null
    const r = lb.getBoundingClientRect()
    return { over: lb.scrollHeight - lb.clientHeight, x: r.x + r.width / 2, y: r.y + r.height / 2 }
  }, PALETTE)
  if (!box) return { skip: 'the palette is not open — nothing to scroll (see the preceding step)' }
  if (box.over < 40) return { skip: `the palette list overflows by only ${box.over}px — no scroll to drive` }
  await page.mouse.move(box.x, box.y)
  for (let i = 0; i < 6; i++) { await page.mouse.wheel(0, 140); await sleep(90) }
  for (let i = 0; i < 10; i++) { await page.keyboard.press('ArrowDown'); await sleep(60) }
  await page.keyboard.press('Escape')
  await sleep(450)
  return true
}

async function scrollTallestScroller(page) {
  const box = await page.evaluate(() => {
    let best = null
    for (const el of document.querySelectorAll('*')) {
      const over = el.scrollHeight - el.clientHeight
      if (over < 200 || el.clientHeight < 120) continue
      const s = getComputedStyle(el)
      if (!/auto|scroll/.test(s.overflowY)) continue
      if (!best || over > best.over) {
        const r = el.getBoundingClientRect()
        best = { over, x: r.x + r.width / 2, y: r.y + Math.min(r.height / 2, 300) }
      }
    }
    return best
  })
  if (!box) return { skip: 'no element scrolls by >200px on this surface — nothing to scroll' }
  await page.mouse.move(box.x, box.y)
  for (let i = 0; i < 8; i++) { await page.mouse.wheel(0, 220); await sleep(90) }
  for (let i = 0; i < 8; i++) { await page.mouse.wheel(0, -220); await sleep(90) }
  return true
}

async function hoverSweep(page) {
  const boxes = await page.evaluate(() => {
    const out = []
    for (const el of document.querySelectorAll('button, a[href], [role="button"], [role="tab"]')) {
      const r = el.getBoundingClientRect()
      if (r.width > 8 && r.height > 8 && r.y > 0 && r.y < innerHeight - 8) {
        out.push({ x: r.x + r.width / 2, y: r.y + r.height / 2 })
      }
      if (out.length >= 14) break
    }
    return out
  })
  if (!boxes.length) return { skip: 'no on-screen controls to hover' }
  for (const b of boxes) { await page.mouse.move(b.x, b.y); await sleep(70) }
  return true
}

async function disclosureCycle(page) {
  const pick = await page.evaluate(() => {
    const shell = (el) => el.closest('nav') || el.closest('header') || el.closest('[data-tour="rail"]')
    for (const el of [...document.querySelectorAll('[aria-expanded="false"]')].reverse()) {
      if (shell(el)) continue
      const r = el.getBoundingClientRect()
      if (r.width < 8 || r.height < 8 || r.y < 0 || r.y > innerHeight - 8) continue
      return {
        label: (el.getAttribute('aria-label') || el.textContent || '(unnamed)').trim().slice(0, 48),
        x: r.x + r.width / 2, y: r.y + r.height / 2,
      }
    }
    return null
  })
  if (!pick) return { skip: 'no on-screen [aria-expanded="false"] outside the nav/top bar — no surface disclosure to drive' }
  await page.mouse.click(pick.x, pick.y)
  await sleep(650)
  await page.keyboard.press('Escape')
  await sleep(450)
  return { detail: `drove "${pick.label}"` }
}

async function composerSlashMenu(page) {
  const cm = page.locator('[contenteditable="true"]').first()
  if (!(await cm.count())) return { skip: 'no contenteditable composer on this surface' }
  await cm.click()
  await cm.pressSequentially('motion budget probe', { delay: 25 })
  await sleep(200)
  await page.keyboard.press('Enter').catch(() => {})
  await sleep(200)
  await cm.pressSequentially('/')
  await page.locator('[role="listbox"]').first().waitFor({ state: 'visible', timeout: 4000 }).catch(() => {})
  await sleep(400)
  await page.keyboard.press('Escape')
  await sleep(200)
  return true
}

const SURFACES = [
  {
    id: 'chat',
    label: 'ChatPage',
    from: '#/dashboard',
    route: '#/chat',
    expectHash: /^#\/chat(\/|$)/,
    steps: [
      ['route crossfade + entrance cascade', navigateIn],
      ['command palette open (overlayEnter)', paletteOpen],
      ['palette list scroll + close', paletteScrollAndClose],
      ['composer typing + slash menu', composerSlashMenu],
      ['surface disclosure open/close', disclosureCycle],
      ['hover sweep over controls', hoverSweep],
      ['wheel scroll the surface scroller', scrollTallestScroller],
    ],
  },
  {
    id: 'loop-cockpit',
    label: 'Loop cockpit',
    from: '#/dashboard',
    route: '#/loops/a17c3f92',
    expectHash: /^#\/loops\/a17c3f92(\?|$)/,
    steps: [
      ['route crossfade + entrance cascade', navigateIn],
      ['surface disclosure open/close', disclosureCycle],
      ['hover sweep over controls', hoverSweep],
      ['wheel scroll the surface scroller', scrollTallestScroller],
      ['command palette open (overlayEnter)', paletteOpen],
      ['palette list scroll + close', paletteScrollAndClose],
    ],
  },
]


const COLLECTOR = () => {
  const st = { t: [], marks: [], animPeak: 0, animFrames: 0, stop: false, i: 0 }
  window.__fm7 = st
  const tick = (ts) => {
    st.t.push(ts)
    if ((st.i++ % 3) === 0 && typeof document.getAnimations === 'function') {
      const n = document.getAnimations().filter((a) => a.playState === 'running').length
      if (n > 0) st.animFrames++
      if (n > st.animPeak) st.animPeak = n
    }
    if (!st.stop) requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
}

async function startCollector(page) {
  await page.evaluate(COLLECTOR)
}

async function mark(page, label) {
  await page.evaluate((l) => { window.__fm7?.marks.push({ label: l, t: performance.now() }) }, label)
}

function deltasOf(timestamps) {
  const d = []
  for (let i = 1; i < timestamps.length; i++) d.push({ ms: timestamps[i] - timestamps[i - 1], at: timestamps[i] })
  return d
}

function stats(deltas) {
  const d = deltas.map((x) => x.ms)
  if (!d.length) return { frames: 0 }
  const sorted = [...d].sort((a, b) => a - b)
  const q = (p) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))]
  const sum = d.reduce((a, b) => a + b, 0)
  const r2 = (n) => Math.round(n * 100) / 100
  return {
    frames: d.length,
    spanMs: r2(sum),
    meanMs: r2(sum / d.length),
    fps: r2(1000 / (sum / d.length)),
    p50Ms: r2(q(0.5)),
    p95Ms: r2(q(0.95)),
    p99Ms: r2(q(0.99)),
    worstMs: r2(sorted[sorted.length - 1]),
    over16_7: d.filter((x) => x > 16.7).length,
    over33_3: d.filter((x) => x > 33.3).length,
    jank: r2(sorted[sorted.length - 1]) > 50,
  }
}

async function readCollector(page) {
  const raw = await page.evaluate(() => {
    const st = window.__fm7
    st.stop = true
    return { t: st.t, marks: st.marks, animPeak: st.animPeak, animFrames: st.animFrames }
  })
  const deltas = deltasOf(raw.t)
  const out = { ...stats(deltas), animationsPeak: raw.animPeak, framesWithAnimation: raw.animFrames }
  if (raw.marks.length) {
    out.perStep = raw.marks
      .map((m, i) => {
        const until = raw.marks[i + 1]?.t ?? Infinity
        return { step: m.label, ...stats(deltas.filter((x) => x.at >= m.t && x.at < until)) }
      })
      .filter((s) => s.frames > 0)
  }
  return out
}

const SHELL = 'nav[data-tour="rail"]'

async function assertSurface(page, surface) {
  const hash = await page.evaluate(() => location.hash)
  if (!surface.expectHash.test(hash)) {
    throw new Error(`${surface.id}: measured hash is ${hash || '(empty)'}, expected ${surface.expectHash} — ` +
      `the probe is not on the surface it claims (a route can canonicalise: #/loops → #/loop)`)
  }
  const info = await page.evaluate((sel) => ({
    elements: document.querySelectorAll('*').length,
    shell: !!document.querySelector(sel),
  }), SHELL)
  if (!info.shell || info.elements < 200) {
    throw new Error(`${surface.id}: shell=${info.shell} elements=${info.elements} — this is the ` +
      `onboarding screen or an unbuilt dist, not the route under test. Any clean frame ` +
      `distribution measured here is meaningless.`)
  }
  return { hash, elements: info.elements }
}

async function measureSurface(browser, opts, surface, bounciness) {
  const context = await browser.newContext({ viewport: opts.viewport, deviceScaleFactor: 2 })
  await context.addInitScript((b) => {
    try {
      const raw = localStorage.getItem('appearance')
      const ov = raw ? JSON.parse(raw) : {}
      ov.scalars = { ...(ov.scalars || {}), '--bounciness': b }
      localStorage.setItem('appearance', JSON.stringify(ov))
      localStorage.setItem('mode', 'dark')
    } catch {   }
  }, bounciness)
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e.message || e)))

  let cdp = null
  if (opts.cpuThrottle > 1) {
    cdp = await context.newCDPSession(page)
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: opts.cpuThrottle })
  }

  const results = []
  try {
    await page.goto(`${opts.url}/${surface.from}`, { waitUntil: 'domcontentloaded' })
    await page.waitForSelector(SHELL, { timeout: 20000 })
    await page.evaluate(() => document.fonts?.ready)
    await sleep(1200)

    await startCollector(page)
    await sleep(opts.windowMs)
    const idle = await readCollector(page)
    results.push({ window: 'idle-control', surface: `${surface.id} (at ${surface.from})`, ...idle })

    await startCollector(page)
    const t0 = Date.now()
    const steps = []
    for (const [label, fn] of surface.steps) {
      await mark(page, label)
      const r = await fn(page, surface).catch((e) => ({ skip: `threw: ${String(e.message || e).split('\n')[0]}` }))
      steps.push({
        step: label,
        ran: r === true || !!(r && !r.skip),
        ...(r && r.skip ? { skip: r.skip } : {}),
        ...(r && r.detail ? { detail: r.detail } : {}),
      })
      if (opts.injectStall > 0 && label.startsWith('hover')) {
        await mark(page, `INJECTED STALL ${opts.injectStall}ms (falsification)`)
        await page.evaluate((ms) => { const end = performance.now() + ms; while (performance.now() < end) {   } }, opts.injectStall)
        await sleep(300)
      }
    }
    const active = await readCollector(page)
    const seen = await assertSurface(page, surface)
    results.push({
      window: 'interaction',
      surface: surface.id,
      ...active,
      elapsedMs: Date.now() - t0,
      steps,
      provokedMotion: active.framesWithAnimation > 0,
      ...seen,
    })
  } finally {
    if (cdp) await cdp.detach().catch(() => {})
    await context.close()
  }
  return { results, pageErrors: errors }
}

async function main() {
  const opts = parseArgs(process.argv)
  const wanted = opts.surfaces ? SURFACES.filter((s) => opts.surfaces.includes(s.id)) : SURFACES
  if (!wanted.length) { console.error(`no surfaces matched ${opts.surfaces}`); process.exit(2) }

  const { executablePath, label } = resolveBrowser(opts)
  const browser = await chromium.launch({ headless: !opts.headed, executablePath })
  const report = {
    env: {
      browser: label,
      browserVersion: browser.version(),
      headless: !opts.headed,
      viewport: `${opts.viewport.width}x${opts.viewport.height}`,
      deviceScaleFactor: 2,
      cpuThrottlingRate: opts.cpuThrottle,
      injectedStallMs: opts.injectStall,
      platform: `${os.platform()} ${os.arch()} ${os.release()}`,
      cpus: `${os.cpus().length}x ${os.cpus()[0]?.model ?? 'unknown'}`,
      node: process.version,
      url: opts.url,
      when: new Date().toISOString(),
      caveat: opts.cpuThrottle > 1
        ? `CPU throttled ${opts.cpuThrottle}x via CDP Emulation.setCPUThrottlingRate`
        : 'UNTHROTTLED headless — a generous environment, not a guarantee for user hardware',
    },
    runs: [],
  }

  let failed = false
  try {
    for (const bounciness of opts.bounciness) {
      for (const surface of wanted) {
        process.stderr.write(`[motion-budget] ${surface.id} @ bounciness=${bounciness}…\n`)
        try {
          const { results, pageErrors } = await measureSurface(browser, opts, surface, bounciness)
          report.runs.push({ bounciness, surface: surface.id, label: surface.label, pageErrors, windows: results })
        } catch (e) {
          failed = true
          report.runs.push({ bounciness, surface: surface.id, label: surface.label, error: String(e.message || e) })
          process.stderr.write(`[motion-budget] FAIL ${surface.id} @ ${bounciness}: ${e.message}\n`)
        }
      }
    }
  } finally {
    await browser.close()
  }

  console.log(JSON.stringify(report, null, 2))

  process.stderr.write('\n[motion-budget] summary (frames / mean / p95 / worst / >16.7ms)\n')
  for (const run of report.runs) {
    if (run.error) { process.stderr.write(`  b=${run.bounciness} ${run.surface}: ERROR ${run.error}\n`); continue }
    for (const w of run.windows) {
      process.stderr.write(
        `  b=${run.bounciness} ${w.window.padEnd(16)} ${String(run.surface).padEnd(13)} ` +
        `${String(w.frames).padStart(4)}f  mean ${String(w.meanMs).padStart(6)}ms (${w.fps}fps)  ` +
        `p95 ${String(w.p95Ms).padStart(6)}ms  worst ${String(w.worstMs).padStart(7)}ms  ` +
        `>16.7ms ${String(w.over16_7).padStart(3)}  >33.3ms ${String(w.over33_3).padStart(3)}` +
        `${w.jank ? '  JANK' : ''}${w.provokedMotion === false ? '  NO-MOTION-PROVOKED' : ''}\n`,
      )
    }
  }
  if (failed) process.exitCode = 1
}

main().catch((e) => { console.error(`[motion-budget] ${e.stack || e}`); process.exit(1) })
