#!/usr/bin/env node

import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync, symlinkSync, readFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

import {
  LEGS, STATUS, newLegs, passLeg, failLeg, skipLeg, noteLeg,
  shapeBundleReport, shapeReport, formatReport,
} from './lib/app_validate_report.mjs'
import { fillRequiredArgs } from './lib/app_validate_form.mjs'

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
const SHELL_SELECTOR = 'nav[data-tour="rail"]'
const ENV_LACKING = [
  /not authenticated/i, /gh auth/i, /auth(entication)? (required|failed)/i,
  /api[_ -]?key/i, /credential/i, /unauthorized/i, /\b401\b/, /\b403\b/,
  /no model provider/i, /model .*not (configured|available|found)/i,
  /connection refused/i, /ECONNREFUSED/, /could not connect/i,
  /not configured/i, /command not found/i, /executable .*not found/i,
  /no such file or directory: '?(gh|git|npm)/i,
]

function log(msg) { console.log(`[app-ui-validate] ${msg}`) }
function warn(msg) { console.error(`[app-ui-validate] ${msg}`) }


function parseArgs(argv) {
  const opts = {
    bundles: [], out: '', port: 0, python: '', appsRoot: '',
    homeBase: process.env.GIDEON_HARNESS_HOME_BASE || os.tmpdir(),
    modelEndpoint: process.env.GIDEON_HARNESS_MODEL_ENDPOINT || 'http://127.0.0.1:11434',
    model: process.env.GIDEON_HARNESS_MODEL || '',
    modelApp: process.env.GIDEON_HARNESS_MODEL_APP || 'ollama-models',
    modelType: process.env.GIDEON_HARNESS_MODEL_TYPE || 'ollama',
    noModel: false, keep: false, headed: false,
  }
  const takesValue = new Set(['--out', '--port', '--python', '--apps-root', '--home-base',
    '--model-endpoint', '--model', '--model-app', '--model-type'])
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (takesValue.has(a)) {
      const v = argv[++i]
      if (v === undefined) throw new Error(`${a} needs a value`)
      if (a === '--out') opts.out = path.resolve(v)
      else if (a === '--port') opts.port = Number(v)
      else if (a === '--python') opts.python = path.resolve(v)
      else if (a === '--apps-root') opts.appsRoot = path.resolve(v)
      else if (a === '--home-base') opts.homeBase = path.resolve(v)
      else if (a === '--model-endpoint') opts.modelEndpoint = v
      else if (a === '--model') opts.model = v
      else if (a === '--model-app') opts.modelApp = v
      else if (a === '--model-type') opts.modelType = v
    } else if (a === '--no-model') opts.noModel = true
    else if (a === '--keep') opts.keep = true
    else if (a === '--headed') opts.headed = true
    else if (a.startsWith('--')) throw new Error(`unknown option ${a}`)
    else opts.bundles.push(path.resolve(a))
  }
  if (!opts.bundles.length) throw new Error('at least one bundle directory is required')
  return opts
}


async function freePort() {
  for (let attempt = 0; attempt < 20; attempt++) {
    const port = await new Promise((resolve, reject) => {
      const srv = createServer()
      srv.once('error', reject)
      srv.listen(0, '127.0.0.1', () => {
        const p = srv.address().port
        srv.close(() => resolve(p))
      })
    })
    if (port !== 10000 && port >= 1024) return port
  }
  throw new Error('could not find a free ephemeral port')
}

function resolvePython(explicit) {
  const candidates = [
    explicit,
    process.env.GIDEON_HARNESS_PYTHON,
    path.join(REPO_ROOT, '.venv', 'bin', 'python'),
  ].filter(Boolean)
  for (const c of candidates) if (existsSync(c)) return c
  return 'python3'
}

function startGateway({ python, home, port, firstPartyDir, logSink }) {
  const env = {
    ...process.env,
    GIDEON_HOME: home,
    GIDEON_WORKSPACE: path.join(home, 'workspace'),
    GIDEON_FIRST_PARTY_APPS_DIR: firstPartyDir,
    PYTHONPATH: [path.join(REPO_ROOT, 'runtime'), process.env.PYTHONPATH].filter(Boolean).join(':'),
  }
  const proc = spawn(python, ['-m', 'gideon', 'gateway', '--port', String(port), '--no-open', '--json-ready'],
    { cwd: REPO_ROOT, env, stdio: ['ignore', 'pipe', 'pipe'] })

  return new Promise((resolve, reject) => {
    let buf = ''
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      proc.kill('SIGKILL')
      reject(new Error(`gateway did not report ready within 180s. Output:\n${buf.slice(-4000)}`))
    }, 180_000)

    const onChunk = (chunk) => {
      const text = String(chunk)
      buf += text
      logSink.push(text)
      const m = buf.match(/GIDEON_READY:\s*(\{.*\})/)
      if (m && !settled) {
        settled = true
        clearTimeout(timer)
        let token = ''
        try { token = JSON.parse(m[1]).token ?? '' } catch {   }
        resolve({ proc, token })
      }
    }
    proc.stdout.on('data', onChunk)
    proc.stderr.on('data', onChunk)
    proc.on('exit', (code) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      reject(new Error(`gateway exited with code ${code} before reporting ready. Output:\n${buf.slice(-4000)}`))
    })
  })
}

async function stopGateway(proc) {
  if (!proc || proc.exitCode !== null) return
  proc.kill('SIGTERM')
  await new Promise((resolve) => {
    const t = setTimeout(() => { proc.kill('SIGKILL'); resolve() }, 10_000)
    proc.once('exit', () => { clearTimeout(t); resolve() })
  })
}


function apiClient(base, token) {
  const call = async (method, route, body) => {
    const sep = route.includes('?') ? '&' : '?'
    const res = await fetch(`${base}${route}${sep}token=${encodeURIComponent(token)}`, {
      method,
      headers: { 'Content-Type': 'application/json', Origin: base },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    let json = null
    try { json = await res.json() } catch {   }
    return { status: res.status, ok: res.ok, json }
  }
  return {
    get: (r) => call('GET', r),
    post: (r, b) => call('POST', r, b),
    del: (r) => call('DELETE', r),
  }
}


function shotter(page, dir) {
  let n = 0
  mkdirSync(dir, { recursive: true })
  return async (name) => {
    n += 1
    const file = path.join(dir, `${String(n).padStart(2, '0')}-${name}.png`)
    try { await page.screenshot({ path: file, fullPage: false }); return file }
    catch (err) { warn(`screenshot ${name} failed: ${err.message}`); return '' }
  }
}


async function gotoRoute(page, base, route) {
  await page.goto(`${base}/#${route}`, { waitUntil: 'load', timeout: 45_000 })
  await page.locator(SHELL_SELECTOR).waitFor({ state: 'visible', timeout: 20_000 })
  await page.waitForTimeout(500)
}

function cardFor(page, displayName) {
  return page.locator('div').filter({ has: page.locator(`button[aria-label="${displayName} — details"]`) }).last()
}

function sidePanel(page, title) {
  return page.getByRole('region', { name: title })
}

function toolRow(page, name) {
  return page.locator(`button:has(span[title="${name}"])`)
}

async function toolGroupHeader(page, provider) {
  const label = page.getByText(provider, { exact: true }).first()
  if (!(await label.count())) return ''
  return (await label.locator('xpath=..').innerText().catch(() => '')).replace(/\s+/g, ' ').trim()
}

async function openManageSources(page) {
  const candidates = [
    page.locator('button[title="Manage Sources"]'),
    page.locator('button', { hasText: 'Add source' }),
  ]
  for (const candidate of candidates) {
    const visible = candidate.locator('visible=true').first()
    if (!(await visible.count())) continue
    await visible.click({ timeout: 5000 }).catch(() => {})
    const panel = sidePanel(page, 'Manage Sources').first()
    const opened = await panel.waitFor({ state: 'visible', timeout: 5000 }).then(() => true).catch(() => false)
    if (opened) return true
  }
  return false
}

async function closePanel(page) {
  const close = page.getByRole('button', { name: 'Close', exact: true })
  if (await close.count()) { await close.first().click().catch(() => {}) }
  else await page.keyboard.press('Escape')
  await page.waitForTimeout(500)
}


async function driveBundle({ browser, base, api, bundleDir, outDir, home, model }) {
  const bundle = path.basename(bundleDir)
  const legs = newLegs()
  const consoleErrors = []
  const pageErrors = []
  let manifest = null
  let notReached = ''

  try {
    manifest = JSON.parse(readFileSync(path.join(bundleDir, 'app.json'), 'utf8'))
  } catch (err) {
    failLeg(legs, 'store-source', `cannot read ${bundle}/app.json: ${err.message}`)
    return shapeBundleReport({ bundle, path: bundleDir, legs, consoleErrors, pageErrors,
      notReachedReason: 'the bundle manifest could not be read, so nothing could be driven' })
  }
  const appName = manifest.name ?? bundle
  const displayName = manifest.displayName ?? appName

  const stagingRoot = path.join(home, 'harness-sources', bundle)
  mkdirSync(stagingRoot, { recursive: true })
  const link = path.join(stagingRoot, bundle)
  if (!existsSync(link)) symlinkSync(bundleDir, link)

  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 400)) })
  page.on('pageerror', (e) => pageErrors.push(String(e.message).split('\n')[0].slice(0, 400)))
  const shot = shotter(page, path.join(outDir, bundle))

  try {
    await page.goto(`${base}/?token=${encodeURIComponent(api.token)}`, { waitUntil: 'load', timeout: 45_000 })
    await gotoRoute(page, base, '/apps?view=store')

    if (!(await openManageSources(page))) {
      failLeg(legs, 'store-source', 'no "Manage Sources" control was reachable on the Store tab')
      notReached = 'the local source could not be registered, so no later leg could run'
      throw new LegAbort()
    }
    const sourcesPanel = sidePanel(page, 'Manage Sources').first()
    const localInput = sourcesPanel.locator('input[name="app-local-source"]')
    await localInput.waitFor({ state: 'visible', timeout: 10_000 })
    await localInput.fill(stagingRoot)
    await localInput.locator('xpath=following::button[1]').click({ timeout: 10_000 })
    await page.waitForTimeout(1500)
    const sourcesShot = await shot('store-source')
    const registered = (await api.get('/api/apps/local-sources')).json?.sources ?? []
    if (!registered.includes(stagingRoot)) {
      failLeg(legs, 'store-source', `the UI accepted the path but /api/apps/local-sources does not list it (got ${JSON.stringify(registered)})`,
        { screenshots: [sourcesShot] })
      notReached = 'the local source was not registered, so no later leg could run'
      throw new LegAbort()
    }
    const listedInPanel = await page.getByText(stagingRoot, { exact: false }).count()
    passLeg(legs, 'store-source', {
      screenshots: [sourcesShot],
      details: { stagingRoot, listedInPanel: listedInPanel > 0 },
      notes: listedInPanel > 0 ? [] : ['the registered path is not echoed in the Manage Sources panel'],
    })
    await closePanel(page)

    const srcKey = `local:${stagingRoot}`
    const card = cardFor(page, displayName)
    let found = false
    for (let attempt = 0; attempt < 4 && !found; attempt++) {
      if (attempt === 0) await gotoRoute(page, base, `/apps?view=store&ssrc=${encodeURIComponent(srcKey)}`)
      else { await page.reload({ waitUntil: 'load' }); await page.locator(SHELL_SELECTOR).waitFor({ state: 'visible', timeout: 20_000 }) }
      found = await card.first().waitFor({ state: 'visible', timeout: 12_000 }).then(() => true).catch(() => false)
    }
    await page.waitForTimeout(700)
    if (!found) {
      const missShot = await shot('store-card-missing')
      failLeg(legs, 'store-card', `no Store card for "${displayName}" after registering the source`, { screenshots: [missShot] })
      notReached = 'the app never surfaced in the Store, so it could not be installed'
      throw new LegAbort()
    }
    const cardText = (await card.first().innerText()).replace(/\s+/g, ' ').trim()
    const dividerLabel = path.basename(stagingRoot)
    const dividerShown = await page.getByText(dividerLabel, { exact: true }).count()
    const cardShot = await shot('store-card')

    await card.first().click()
    await page.waitForTimeout(900)
    const panel = sidePanel(page, displayName)
    const panelPresent = await panel.count()
    const panelText = panelPresent ? (await panel.first().innerText()).replace(/\s+/g, ' ').trim() : ''
    const detailShot = await shot('store-card-detail')

    const catalogEntry = ((await api.get('/api/apps/catalog')).json?.localApps ?? [])
      .find((e) => e.name === appName) ?? null
    const declaredPerms = Object.entries(manifest.permissions ?? {})
    const missing = []
    if (!cardText.includes(displayName)) missing.push('the card does not show the display name')
    const desc = String(manifest.description ?? '').slice(0, 40)
    if (desc && !cardText.includes(desc.slice(0, 24))) missing.push('the card does not show the manifest description')
    if (!dividerShown) missing.push(`no source divider named "${dividerLabel}" groups the card`)
    if (!panelPresent) missing.push('the detail panel did not open')
    const permsShown = declaredPerms.length === 0
      || /Permissions the gateway enforces|granted no gateway capability/i.test(panelText)
    if (!permsShown) missing.push('the detail panel does not render the declared permissions')
    const typeShown = !catalogEntry?.isProvider || panelText.includes('provider')
    if (!typeShown) missing.push('the detail panel does not name the app type')

    if (missing.length) {
      failLeg(legs, 'store-card', missing.join('; '),
        { screenshots: [cardShot, detailShot], details: { cardText, panelText, sourceKind: catalogEntry?.sourceKind ?? null } })
    } else {
      passLeg(legs, 'store-card', {
        screenshots: [cardShot, detailShot],
        details: {
          cardText, panelText,
          sourceKind: catalogEntry?.sourceKind ?? null,
          providerType: catalogEntry?.providerType ?? '',
          declaredPermissions: Object.fromEntries(declaredPerms),
          sourceDivider: dividerLabel,
        },
      })
    }

    const toolsBefore = new Set((((await api.get('/api/tools')).json?.tools) ?? []).map((t) => `${t.provider}::${t.name}`))
    const installBtn = panel.getByRole('button', { name: 'Install', exact: true })
    if (!(await installBtn.count())) {
      const noBtnShot = await shot('ui-install-no-button')
      failLeg(legs, 'ui-install', 'the Store detail panel offers no Install button', { screenshots: [noBtnShot] })
      notReached = 'the app was never installed, so no post-install leg could run'
      throw new LegAbort()
    }
    await installBtn.first().click()

    const consentModal = page.locator('[role="dialog"]').filter({ hasText: `Install ${displayName}` })
    const anywayBtn = page.getByRole('button', { name: 'Install anyway' })
    let consentShot = ''
    let consentText = ''
    let sawConsent = false
    let installedApp = null
    for (let i = 0; i < 60; i++) {
      if (await anywayBtn.first().isVisible().catch(() => false)) { sawConsent = true; break }
      installedApp = ((await api.get('/api/apps')).json?.apps ?? []).find((a) => a.name === appName) ?? null
      if (installedApp) break
      if (await page.getByText(/cannot be installed|scanner flagged dangerous/i).count()) break
      await page.waitForTimeout(1000)
    }
    if (sawConsent) {
      await page.waitForTimeout(1200)
      consentText = (await consentModal.first().innerText().catch(() => '')).replace(/\s+/g, ' ').trim()
      consentShot = await shot('ui-install-consent')
      noteLeg(legs, 'ui-install', { screenshots: [consentShot], notes: ['the scanner raised a consent dialog; clicked through it as a user'] })
      await anywayBtn.first().click()
    }
    for (let i = 0; i < 90 && !installedApp; i++) {
      await page.waitForTimeout(2000)
      installedApp = ((await api.get('/api/apps')).json?.apps ?? []).find((a) => a.name === appName) ?? null
      if (installedApp) break
      if (await page.getByText(/cannot be installed|scanner flagged dangerous/i).count()) break
    }
    let refusalText = ''
    if (!installedApp) {
      await page.waitForTimeout(1200)
      refusalText = (await consentModal.first().innerText().catch(() => '')).replace(/\s+/g, ' ').trim()
    }
    const installShot = await shot('ui-install-result')
    if (!installedApp) {
      const errText = await page.locator('.text-negative, .text-danger').allInnerTexts().catch(() => [])
      failLeg(legs, 'ui-install', `the app is not installed after clicking Install${sawConsent ? ' and consenting' : ''}${errText.length ? `: ${errText.join(' | ').slice(0, 300)}` : ''}`,
        { screenshots: [installShot], details: { consentText, refusalText } })
      notReached = 'the install never completed, so no post-install leg could run'
      throw new LegAbort()
    }
    passLeg(legs, 'ui-install', {
      screenshots: [installShot],
      details: { consentDialogShown: sawConsent, consentText, version: installedApp.version ?? '' },
    })

    await gotoRoute(page, base, '/apps?view=library')
    const libCard = cardFor(page, displayName)
    const inLibrary = await libCard.first().waitFor({ state: 'visible', timeout: 20_000 })
      .then(() => true).catch(() => false)
    await page.waitForTimeout(900)
    const libShot = await shot('library')

    const toolsAfter = (((await api.get('/api/tools')).json?.tools) ?? [])
    const contributed = toolsAfter.filter((t) => !toolsBefore.has(`${t.provider}::${t.name}`))
    const providers = [...new Set(contributed.map((t) => t.provider))]

    await gotoRoute(page, base, '/tools')
    const renderedToolNames = []
    const groupHeaders = {}
    if (contributed.length) {
      await toolRow(page, contributed[0].name).first().waitFor({ state: 'attached', timeout: 20_000 })
        .catch(() => {})
      for (const t of contributed) {
        if (await toolRow(page, t.name).count()) renderedToolNames.push(t.name)
      }
      for (const p of providers) groupHeaders[p] = await toolGroupHeader(page, p)
    }
    const toolsShot = await shot('library-and-tools')

    if (!inLibrary) {
      failLeg(legs, 'library-and-tools', `the installed app has no card in the Library tab`, { screenshots: [libShot, toolsShot] })
    } else if (!contributed.length) {
      skipLeg(legs, 'library-and-tools', 'the app landed in the Library, but it contributes no tools for the Tools page to render',
        { screenshots: [libShot, toolsShot], details: { library: 'PASS', toolProviders: providers } })
    } else if (!renderedToolNames.length) {
      failLeg(legs, 'library-and-tools', `the app registered ${contributed.length} tool(s) (${contributed.slice(0, 4).map((t) => t.name).join(', ')}) but none render on the Tools page`,
        { screenshots: [libShot, toolsShot], details: { library: 'PASS', toolProviders: providers } })
    } else {
      passLeg(legs, 'library-and-tools', {
        screenshots: [libShot, toolsShot],
        details: { library: 'PASS', toolProviders: providers, toolCount: contributed.length, renderedTools: renderedToolNames, groupHeaders },
      })
    }

    if (!contributed.length) {
      skipLeg(legs, 'tool-invoke', 'the app contributes no tools, so there is nothing to invoke from the UI')
    } else {
      const target = pickInvokableTool(contributed)
      if (!target) {
        skipLeg(legs, 'tool-invoke', `every tool this app contributes requires arguments the harness cannot synthesise (${contributed.map((t) => t.name).join(', ')})`)
      } else {
        const res = await runToolFromUi({ page, base, tool: target, shot })
        const details = { tool: target.name, provider: target.provider, args: res.args, output: res.output.slice(0, 1200) }
        if (res.status === 'ok') {
          passLeg(legs, 'tool-invoke', { screenshots: res.screenshots, details })
        } else if (res.status === 'env') {
          skipLeg(legs, 'tool-invoke', `${target.name} ran from the UI but this environment lacks what it needs: ${res.output.slice(0, 240)}`,
            { screenshots: res.screenshots, details })
        } else if (res.status === 'error') {
          failLeg(legs, 'tool-invoke', `${target.name} ran from the UI and returned an error: ${res.output.slice(0, 240)}`,
            { screenshots: res.screenshots, details })
        } else {
          failLeg(legs, 'tool-invoke', `${target.name} could not be run from the UI: ${res.output.slice(0, 240)}`,
            { screenshots: res.screenshots, details })
        }
      }
    }

    const round = await deactivateReactivate({ page, base, api, appName, displayName, shot })
    if (round.ok) passLeg(legs, 'reactivate', { screenshots: round.screenshots, details: round.details })
    else if (round.skip) skipLeg(legs, 'reactivate', round.skip, { screenshots: round.screenshots, details: round.details })
    else failLeg(legs, 'reactivate', round.reason, { screenshots: round.screenshots, details: round.details })
  } catch (err) {
    if (!(err instanceof LegAbort)) {
      const crashShot = await shot('crash')
      const pending = legs.find((l) => l.status === STATUS.PENDING)
      if (pending) failLeg(legs, pending.id, `harness error: ${err.message.split('\n')[0]}`, { screenshots: [crashShot] })
      notReached = notReached || `the drive stopped early: ${err.message.split('\n')[0]}`
    }
  } finally {
    await page.close().catch(() => {})
    await context.close().catch(() => {})
    await api.del(`/api/apps/${encodeURIComponent(appName)}?force=1`).catch(() => {})
    await api.del(`/api/apps/local-sources?path=${encodeURIComponent(stagingRoot)}`).catch(() => {})
  }

  const report = shapeBundleReport({
    bundle, path: bundleDir, legs, consoleErrors, pageErrors,
    app: { name: appName, displayName, version: manifest.version ?? '' },
    notReachedReason: notReached,
  })
  if (model && !model.configured) {
    for (const leg of report.legs) {
      if (leg.id === 'tool-invoke' && leg.status === STATUS.SKIPPED) {
        leg.reason = `${leg.reason} (no model provider is wired: ${model.reason})`
      }
    }
  }
  return report
}

class LegAbort extends Error {}

function pickInvokableTool(tools) {
  const req = (t) => (t.parameters?.required ?? [])
  const noArgs = tools.filter((t) => req(t).length === 0)
  if (noArgs.length) return noArgs.find((t) => /list|status|ls|show|get|search/i.test(t.name)) ?? noArgs[0]
  const primitive = tools.filter((t) => req(t).every((k) => {
    const s = t.parameters?.properties?.[k]
    return s && ['string', 'integer', 'number', 'boolean'].includes(s.type)
  }))
  return primitive[0] ?? null
}

async function runToolFromUi({ page, base, tool, shot }) {
  const screenshots = []
  await gotoRoute(page, base, '/tools')
  const search = page.getByPlaceholder('Search tools').first()
  if (await search.count()) { await search.fill(tool.name); await page.waitForTimeout(900) }
  const row = toolRow(page, tool.name)
  await row.first().waitFor({ state: 'attached', timeout: 20_000 }).catch(() => {})
  if (!(await row.count())) {
    screenshots.push(await shot('tool-invoke-no-row'))
    return { status: 'blocked', output: `no clickable row for ${tool.name} on the Tools page`, args: {}, screenshots }
  }
  await row.first().click()
  await page.waitForTimeout(800)

  const tryIt = page.getByRole('button', { name: /Try it/ })
  if (!(await tryIt.count())) {
    screenshots.push(await shot('tool-invoke-no-try-it'))
    return { status: 'blocked', output: 'the tool inspector offers no "Try it" panel', args: {}, screenshots }
  }
  await tryIt.first().click()
  await page.waitForTimeout(500)

  const { args, unfilled } = await fillRequiredArgs(page, tool)
  screenshots.push(await shot('tool-invoke-form'))
  if (unfilled.length) {
    return {
      status: 'blocked',
      output: `the harness could not enter ${unfilled.length} required argument(s) in the "Try it" form, so the run would not have tested ${tool.name}: ${unfilled.join('; ')}`,
      args, screenshots,
    }
  }

  const runBtn = page.getByRole('button', { name: /^Run tool$/ })
  if (!(await runBtn.count())) {
    return { status: 'blocked', output: 'the "Try it" panel offers no "Run tool" button', args, screenshots }
  }
  await runBtn.first().click()
  const confirmBtn = page.getByRole('button', { name: /Confirm & run/ })
  await confirmBtn.first().waitFor({ state: 'visible', timeout: 10_000 })
  await confirmBtn.first().click()

  const okBlock = page.getByText('Success', { exact: true })
  const errBlock = page.getByText('Error', { exact: true })
  let outcome = ''
  for (let i = 0; i < 120; i++) {
    await page.waitForTimeout(1000)
    if (await okBlock.count()) { outcome = 'ok'; break }
    if (await errBlock.count()) { outcome = 'error'; break }
  }
  screenshots.push(await shot('tool-invoke-result'))
  if (!outcome) {
    return { status: 'blocked', output: 'the run never produced a Success or Error block (still spinning after 120s)', args, screenshots }
  }
  const panelText = await page.locator('[class*="max-h-96"]').last().innerText().catch(() => '')
  const output = panelText.replace(/\s+/g, ' ').trim()
  if (outcome === 'ok') return { status: 'ok', output, args, screenshots }
  const envLacking = ENV_LACKING.some((re) => re.test(output))
  return { status: envLacking ? 'env' : 'error', output, args, screenshots }
}

async function deactivateReactivate({ page, base, api, appName, displayName, shot }) {
  const screenshots = []
  const details = {}
  await gotoRoute(page, base, '/apps?view=library')
  const card = cardFor(page, displayName)
  await card.first().waitFor({ state: 'attached', timeout: 20_000 }).catch(() => {})
  if (!(await card.count())) {
    screenshots.push(await shot('reactivate-no-card'))
    return { reason: 'the app has no Library card to open, so the state toggle is unreachable', screenshots, details }
  }
  await card.first().click()
  await page.waitForTimeout(900)
  const panel = sidePanel(page, displayName)

  const deactivate = panel.getByRole('button', { name: 'Deactivate', exact: true })
  if (!(await deactivate.count())) {
    screenshots.push(await shot('reactivate-no-toggle'))
    return { reason: 'the app detail panel offers no Deactivate control', screenshots, details }
  }
  await deactivate.first().click()
  let off = false
  for (let i = 0; i < 30; i++) {
    await page.waitForTimeout(1000)
    const app = ((await api.get('/api/apps')).json?.apps ?? []).find((a) => a.name === appName)
    if (app && app.enabled === false) { off = true; break }
  }
  screenshots.push(await shot('reactivate-deactivated'))
  details.deactivated = off
  if (!off) return { reason: 'clicking Deactivate never moved the app out of the enabled state', screenshots, details }

  const activate = panel.getByRole('button', { name: 'Activate', exact: true })
  if (!(await activate.count())) {
    screenshots.push(await shot('reactivate-no-activate'))
    return { reason: 'after deactivating, no Activate control is offered to bring the app back', screenshots, details }
  }
  await activate.first().click()
  let on = false
  for (let i = 0; i < 30; i++) {
    await page.waitForTimeout(1000)
    const app = ((await api.get('/api/apps')).json?.apps ?? []).find((a) => a.name === appName)
    if (app && app.enabled === true) { on = true; break }
  }
  screenshots.push(await shot('reactivate-reactivated'))
  details.reactivated = on
  if (!on) return { reason: 'clicking Activate never brought the app back to the enabled state', screenshots, details }
  return { ok: true, screenshots, details }
}


async function probeModelEndpoint(endpoint) {
  for (const route of ['/v1/models', '/api/tags']) {
    try {
      const res = await fetch(`${endpoint.replace(/\/$/, '')}${route}`, { signal: AbortSignal.timeout(5000) })
      if (res.ok) return { ok: true, route }
    } catch (err) {   }
  }
  return { ok: false, reason: `no model endpoint answered at ${endpoint} (tried /v1/models and /api/tags)` }
}

async function wireModel({ api, opts, appsRoot }) {
  if (opts.noModel) return { configured: false, reason: 'model wiring was disabled with --no-model', endpoint: opts.modelEndpoint }
  const probe = await probeModelEndpoint(opts.modelEndpoint)
  if (!probe.ok) return { configured: false, reason: probe.reason, endpoint: opts.modelEndpoint }

  const appDir = path.isAbsolute(opts.modelApp) ? opts.modelApp : path.join(appsRoot, opts.modelApp)
  if (!existsSync(path.join(appDir, 'app.json'))) {
    return { configured: false, endpoint: opts.modelEndpoint, reason: `the model provider app ${opts.modelApp} is not at ${appDir}` }
  }
  const install = await api.post('/api/apps', { source: appDir, confirm: true })
  if (!install.json?.ok) {
    return { configured: false, endpoint: opts.modelEndpoint, reason: `installing the model provider app failed: ${install.json?.error ?? install.status}` }
  }
  return { configured: false, endpoint: opts.modelEndpoint, appDir, installed: true, reason: 'provider entry not created yet' }
}

async function createModelProvider({ api, opts }) {
  const created = await api.post('/api/model-providers', {
    name: 'harness-model',
    type: opts.modelType,
    model: opts.model,
    options: { endpoint: opts.modelEndpoint },
  })
  if (created.json?.ok || created.status === 200) {
    return { configured: true, endpoint: opts.modelEndpoint, model: opts.model, type: opts.modelType, reason: '' }
  }
  return {
    configured: false, endpoint: opts.modelEndpoint, model: opts.model, type: opts.modelType,
    reason: `could not register the model provider: ${created.json?.error ?? created.status}`,
  }
}


async function main() {
  const opts = parseArgs(process.argv.slice(2))
  if (!existsSync(path.join(REPO_ROOT, 'runtime', 'gideon', 'static', 'dist', 'index.html'))) {
    warn('the gateway has no built SPA to serve (runtime/gideon/static/dist/index.html missing) — run `make web-build`')
    process.exitCode = 2
    return
  }
  for (const b of opts.bundles) {
    if (!existsSync(path.join(b, 'app.json'))) {
      warn(`${b} is not an app bundle (no app.json)`)
      process.exitCode = 2
      return
    }
  }

  const appsRoot = opts.appsRoot || path.dirname(opts.bundles[0])
  const home = mkdtempSync(path.join(opts.homeBase, 'gideon-app-ui-validate-'))
  const outDir = opts.out || path.join(home, 'report')
  mkdirSync(path.join(home, 'workspace'), { recursive: true })
  mkdirSync(outDir, { recursive: true })
  writeFileSync(path.join(home, 'config.json'), JSON.stringify({ dashboard: { user_name: 'Harness' } }))

  const port = opts.port || await freePort()
  if (port === 10000) throw new Error('refusing to bind port 10000')
  const python = resolvePython(opts.python)
  const firstPartyDir = path.join(home, 'no-first-party-apps')
  const gatewayLog = []

  log(`home ${home}`)
  log(`out  ${outDir}`)
  log(`port ${port} · python ${python}`)

  let gateway = null
  let bundles = []
  let model = null
  const onSignal = (sig) => {
    if (gateway) gateway.kill('SIGKILL')
    if (!opts.keep && !outDir.startsWith(home)) rmSync(home, { recursive: true, force: true })
    warn(`interrupted by ${sig} — gateway stopped`)
    process.exit(130)
  }
  process.once('SIGINT', () => onSignal('SIGINT'))
  process.once('SIGTERM', () => onSignal('SIGTERM'))
  try {
    let started = await startGateway({ python, home, port, firstPartyDir, logSink: gatewayLog })
    gateway = started.proc
    let api = { ...apiClient(`http://127.0.0.1:${port}`, started.token), token: started.token }
    model = await wireModel({ api, opts, appsRoot })
    if (model.installed) {
      await stopGateway(gateway)
      started = await startGateway({ python, home, port, firstPartyDir, logSink: gatewayLog })
      gateway = started.proc
      api = { ...apiClient(`http://127.0.0.1:${port}`, started.token), token: started.token }
      model = await createModelProvider({ api, opts })
    }
    log(model.configured ? `model: ${model.type} → ${model.endpoint}${model.model ? ` (${model.model})` : ''}` : `model: NOT wired — ${model.reason}`)

    const base = `http://127.0.0.1:${port}`
    const browser = await chromium.launch({ headless: !opts.headed })
    try {
      for (const bundleDir of opts.bundles) {
        log(`driving ${path.basename(bundleDir)}…`)
        const report = await driveBundle({ browser, base, api, bundleDir, outDir, home, model })
        bundles.push(report)
        log(`  ${report.bundle}: ${report.verdict}`)
      }
    } finally {
      await browser.close().catch(() => {})
    }
  } finally {
    await stopGateway(gateway)
    const report = shapeReport({
      bundles,
      harness: { script: 'tooling/scripts/app_ui_validate.mjs', port, home, outDir, python, repoRoot: REPO_ROOT },
      model,
    })
    const reportPath = path.join(outDir, 'report.json')
    writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`)
    if (opts.keep) writeFileSync(path.join(outDir, 'gateway.log'), gatewayLog.join(''))
    console.log(formatReport(report))
    log(`report ${reportPath}`)
    if (!opts.keep) {
      if (!outDir.startsWith(home)) rmSync(home, { recursive: true, force: true })
      else log(`kept ${home} (it holds the report)`)
    } else {
      log(`kept ${home}`)
    }
    process.exitCode = report.exitCode
  }
}

main().catch((err) => {
  warn(err.stack ?? String(err))
  process.exitCode = 1
})
