import { existsSync, readFileSync, realpathSync } from 'node:fs'
import { isAbsolute, relative, resolve, sep } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { gotoRoute } from './helpers'

type Action = 'download' | 'cancel' | 'repair' | 'warning-error'
type DownloadState = 'queued' | 'running' | 'done' | 'error' | 'cancelled'
type ModelRow = {
  name: string
  id?: string
  downloaded?: boolean
  integrity?: string
  capabilities?: string[]
  gated?: boolean
}
type ProviderRow = { name: string; local?: boolean; models?: ModelRow[] }
type Job = {
  id: string
  provider: string
  model: string
  state: DownloadState
  progress: number
  downloaded_bytes: number
  total_bytes: number
  error?: string
  reason?: string
  warning?: string
}

function required(name: string): string {
  const value = process.env[name]?.trim()
  if (!value) throw new Error(`LIVE_MODEL_DOWNLOAD_PREREQUISITE: ${name} must be set; this gate never skips missing live configuration.`)
  return value
}

const action = required('GIDEON_E2E_MODEL_ACTION') as Action
if (!['download', 'cancel', 'repair', 'warning-error'].includes(action)) {
  throw new Error('LIVE_MODEL_DOWNLOAD_PREREQUISITE: GIDEON_E2E_MODEL_ACTION must be download, cancel, repair, or warning-error.')
}
if (process.env.PW_NO_SERVER !== '1') {
  throw new Error('LIVE_MODEL_DOWNLOAD_PREREQUISITE: set PW_NO_SERVER=1 so this test cannot start the scripted gateway.')
}
const baseUrl = required('PW_BASE_URL')
const base = new URL(baseUrl)
if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname)) {
  throw new Error('LIVE_MODEL_DOWNLOAD_PREREQUISITE: PW_BASE_URL must point to an HTTP loopback Vite dev server proxying the real gateway.')
}
required('PW_TOKEN')
const providerName = required('GIDEON_E2E_MODEL_PROVIDER')
const modelName = required('GIDEON_E2E_MODEL_NAME')
const useCase = process.env.GIDEON_E2E_MODEL_USE_CASE?.trim() || ''
if (['repair', 'warning-error'].includes(action) && !useCase) {
  throw new Error('LIVE_MODEL_DOWNLOAD_PREREQUISITE: GIDEON_E2E_MODEL_USE_CASE is required for the real Models-page Repair or warning journey.')
}
if (action === 'warning-error') {
  const providerSource = process.env.GIDEON_TEST_LOCAL_MODEL_APP_SOURCE?.trim()
  if (!providerSource || !existsSync(providerSource)) {
    throw new Error('LIVE_MODEL_DOWNLOAD_PREREQUISITE: GIDEON_TEST_LOCAL_MODEL_APP_SOURCE must point to the genuine provider source used by the local gateway.')
  }
}

const homeInput = required('GIDEON_HOME')
if (!existsSync(homeInput)) {
  throw new Error('LIVE_MODEL_DOWNLOAD_PREREQUISITE: GIDEON_HOME must exist and be dedicated to this model-download run.')
}
const home = realpathSync(homeInput)
const cacheDirInput = required('GIDEON_E2E_MODEL_CACHE_ROOT')
const cacheDir = resolve(cacheDirInput)
const marker = resolve(home, '.model-download-e2e-isolated')
if (!isAbsolute(cacheDirInput) || !existsSync(cacheDir) || !existsSync(marker)) {
  throw new Error('LIVE_MODEL_DOWNLOAD_PREREQUISITE: create the isolated model cache and GIDEON_HOME/.model-download-e2e-isolated marker before running this gate.')
}
const relativeCache = relative(home, realpathSync(cacheDir))
if (relativeCache === '..' || relativeCache.startsWith(`..${sep}`) || isAbsolute(relativeCache)) {
  throw new Error('LIVE_MODEL_DOWNLOAD_PREREQUISITE: GIDEON_E2E_MODEL_CACHE_ROOT must resolve inside the isolated GIDEON_HOME.')
}
if (readFileSync(marker, 'utf8').trim() !== 'isolated model download e2e') {
  throw new Error('LIVE_MODEL_DOWNLOAD_PREREQUISITE: the isolated home marker must contain exactly "isolated model download e2e".')
}

async function browserJson<T>(page: Page, path: string, method = 'GET', body?: unknown): Promise<{ status: number; value: T }> {
  return page.evaluate(async ({ path, method, body }) => {
    const response = await fetch(path, {
      method,
      headers: {
        'X-Session-Key': 'dashboard:ui',
        'X-Gideon-API-Version': '1',
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    return { status: response.status, value: await response.json() }
  }, { path, method, body }) as Promise<{ status: number; value: T }>
}

async function listJobs(page: Page): Promise<Job[]> {
  const response = await browserJson<{ downloads?: Job[] }>(page, '/api/models/downloads')
  expect(response.status, 'the real gateway must answer the model-download job API').toBe(200)
  return response.value.downloads ?? []
}

async function waitForState(page: Page, id: string, states: DownloadState[], timeoutMs: number): Promise<Job> {
  const deadline = Date.now() + timeoutMs
  let last: Job | undefined
  while (Date.now() < deadline) {
    last = (await listJobs(page)).find((job) => job.id === id)
    if (last && states.includes(last.state)) return last
    await page.waitForTimeout(750)
  }
  throw new Error(`Live model download job ${id} did not reach ${states.join('/')} before timeout; last state: ${last?.state ?? 'missing'}.`)
}

async function waitForJobAbsence(page: Page, id: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (!(await listJobs(page)).some((job) => job.id === id)) return
    await page.waitForTimeout(500)
  }
  throw new Error(`Cancelled model download job ${id} remained in the real gateway registry.`)
}

async function waitForTerminalSse(page: Page, id: string, timeoutMs: number): Promise<{ event: string; job: Job }> {
  return page.evaluate(({ id, timeoutMs }) => new Promise<{ event: string; job: Job }>((resolve, reject) => {
    const stream = new EventSource(`/api/models/downloads/${encodeURIComponent(id)}/stream`)
    const timer = window.setTimeout(() => {
      stream.close()
      reject(new Error(`Real model-download SSE ${id} did not produce a terminal frame.`))
    }, timeoutMs)
    const finish = (event: Event) => {
      let job: Job
      try { job = JSON.parse((event as MessageEvent).data) as Job } catch { return }
      if (!['done', 'error', 'cancelled'].includes(job.state)) return
      window.clearTimeout(timer)
      stream.close()
      resolve({ event: event.type, job })
    }
    for (const name of ['snapshot', 'progress', 'done', 'error', 'cancelled']) stream.addEventListener(name, finish)
  }), { id, timeoutMs }) as Promise<{ event: string; job: Job }>
}

const USE_CASE_LABELS: Record<string, string> = {
  chat: 'Chat', code_tools: 'Code & tools', reasoning: 'Reasoning', background: 'Background',
  orchestration: 'Orchestration', loops: 'Loops', embedding: 'Embedding', stt: 'Speech-to-text',
  tts: 'Text-to-speech', diarization: 'Speaker diarization', image_modality: 'Image · Modality',
  image_gen: 'Image · Generation', audio_modality: 'Audio · Modality', audio_gen: 'Audio · Generation',
  video_modality: 'Video · Modality', video_gen: 'Video · Generation',
}

test.describe('real local model download and repair', () => {
  test.describe.configure({ timeout: 15 * 60 * 1000 })

  test('uses the real catalog, download job, SSE stream, and settings row across reload', async ({ page }) => {
    const fatalPageErrors: string[] = []
    page.setDefaultNavigationTimeout(30_000)
    page.setDefaultTimeout(20_000)
    page.on('pageerror', (error) => fatalPageErrors.push(error.message))
    page.on('console', (message) => {
      if (message.type() === 'error' && /failed to load (?:dynamically imported )?module|failed to fetch dynamically imported module|failed to resolve import|internal server error/i.test(message.text())) {
        fatalPageErrors.push(message.text())
      }
    })
    page.on('response', (response) => {
      if (response.status() >= 500 && response.request().resourceType() === 'script') {
        fatalPageErrors.push(`Script request failed with HTTP ${response.status()}: ${new URL(response.url()).pathname}`)
      }
    })
    const failOnFatalPageErrors = () => expect(fatalPageErrors, 'the Settings page must not have fatal runtime or module-load errors').toEqual([])

    try {
      if (action === 'repair' || action === 'warning-error') await gotoRoute(page, 'settings/models')
      else await page.goto(`${baseUrl}/#/settings/providers`, { waitUntil: 'domcontentloaded' })
    } catch (error) {
      if (fatalPageErrors.length > 0) {
        throw new Error(`Fatal Settings page/module error during navigation: ${fatalPageErrors.join(' | ')}`, { cause: error })
      }
      throw error
    }
    failOnFatalPageErrors()

    const available = await browserJson<{ providers?: ProviderRow[] }>(page, '/api/models/available')
    expect(available.status, 'the selected live gateway must expose the actual local-model catalog').toBe(200)
    const provider = (available.value.providers ?? []).find((row) => row.name === providerName)
    expect(provider, `real catalog has no provider named ${providerName}`).toBeTruthy()
    expect(provider?.local, `provider ${providerName} must be a native local provider`).toBe(true)
    const model = provider?.models?.find((row) => row.name === modelName)
    expect(model, `real catalog ${providerName} has no selected model ${modelName}`).toBeTruthy()
    if (action === 'repair') {
      expect(model?.integrity, 'Repair must be exercised against a genuinely truncated downloaded model').toBe('truncated')
      expect(model?.downloaded, 'the truncated model must exist in the real provider cache before repair').toBe(true)
      expect(model?.capabilities, `selected model must be exposed under the real ${useCase} use case`).toContain(useCase)
    } else if (action === 'warning-error') {
      expect(model?.downloaded, 'the genuine no-token provider model must be absent before the production API request').toBe(false)
      expect(model?.gated, 'the real provider catalog must retain its gated-model declaration').toBe(true)
      expect(model?.capabilities, `selected model must be exposed under the real ${useCase} use case`).toContain(useCase)
    } else {
      expect(model?.downloaded, 'Download/Cancel requires a fresh-cache model absent from the real provider cache').toBe(false)
    }

    const beforeJobs = await listJobs(page)
    expect(beforeJobs.some((job) => job.provider === providerName && job.model === modelName && ['queued', 'running'].includes(job.state)),
      'an existing live job would make this a re-attachment test rather than a fresh user start').toBe(false)

    if (action === 'repair' || action === 'warning-error') {
      const label = USE_CASE_LABELS[useCase]
      expect(label, `unsupported Models-page use case ${useCase}`).toBeTruthy()
      const useCaseDisclosure = page.getByRole('button', { name: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`) })
      await expect(useCaseDisclosure, 'the real Settings Models use-case row must become visible').toBeVisible({ timeout: 30_000 })
      failOnFatalPageErrors()
      await useCaseDisclosure.click({ timeout: 30_000 })
      failOnFatalPageErrors()
      await page.getByRole('button', { name: modelName, exact: true }).waitFor({ state: 'visible' })
      if (action === 'warning-error') {
        const gatedAction = page.getByRole('button', { name: `Download ${modelName}`, exact: true })
        if (await gatedAction.count()) {
          await expect(gatedAction).toBeDisabled()
        }
      }
    } else {
      await page.getByRole('button', { name: `Download ${modelName}`, exact: true }).waitFor({ state: 'visible', timeout: 30_000 })
    }

    const streamRequests: string[] = []
    page.on('request', (request) => {
      if (request.url().includes('/api/models/downloads/') && request.url().endsWith('/stream')) streamRequests.push(request.url())
    })
    let created: Job
    if (action === 'warning-error') {
      const post = await browserJson<Job>(page, '/api/models/downloads', 'POST', { provider: providerName, model: modelName })
      expect(post.status, 'the real provider-generic download API must accept the request despite the intentionally disabled token-gated UI control').toBe(202)
      created = post.value
      expect(created.warning, 'the real provider cache_dir=None must attach the authentic unknown-filesystem warning').toContain('Free space could not be checked')
    } else {
      const startResponse = page.waitForResponse((response) =>
        response.url().includes('/api/models/downloads') && response.request().method() === 'POST')
      if (action === 'repair') await page.getByRole('button', { name: 'Repair', exact: true }).click()
      else await page.getByRole('button', { name: `Download ${modelName}`, exact: true }).click()
      const post = await startResponse
      expect(post.status(), 'real POST /api/models/downloads must create the selected job').toBe(202)
      created = await post.json() as Job
    }
    expect(created.id).toBeTruthy()
    expect(created.provider).toBe(providerName)
    expect(created.model).toBe(modelName)
    expect(['queued', 'running', 'done', 'error']).toContain(created.state)
    if (action === 'cancel' && !['queued', 'running'].includes(created.state)) {
      throw new Error(`The real model reached ${created.state} before the reloaded Cancel journey could begin.`)
    }

    if (action === 'warning-error') {
      const terminalFrame = await waitForTerminalSse(page, created.id, 30_000)
      expect(terminalFrame.job.state, 'the real tokenless provider must fail through its production download job').toBe('error')
      expect(terminalFrame.job.warning).toBe(created.warning)
      expect(terminalFrame.job.reason).toBeTruthy()
      await expect.poll(() => streamRequests.length, { timeout: 20_000, message: 'the browser must receive the real job SSE stream' }).toBeGreaterThan(0)

      await page.reload()
      failOnFatalPageErrors()
      const persistedJobs = await listJobs(page)
      const terminal = persistedJobs.find((job) => job.id === created.id)
      if (!terminal || terminal.state !== 'error' || !terminal.error) {
        throw new Error(`The real warning job did not persist its terminal error after reload; state=${terminal?.state ?? 'missing'}.`)
      }
      expect(terminal.warning).toBe(created.warning)

      const label = USE_CASE_LABELS[useCase]
      const useCaseDisclosure = page.getByRole('button', { name: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`) })
      await expect(useCaseDisclosure).toBeVisible({ timeout: 30_000 })
      const modelRow = page.getByRole('button', { name: modelName, exact: true })
      if (!(await modelRow.isVisible())) await useCaseDisclosure.click({ timeout: 30_000 })
      const errorRow = page.getByTestId('model-repair')
      await expect(errorRow, 'after reload the actual Models row must retain the failed download').toBeVisible()
      await expect(errorRow.getByRole('alert')).toContainText(terminal.error!)
      await expect(errorRow.getByText(terminal.warning!, { exact: false }), 'the actual precheck warning must remain visible with the terminal error').toBeVisible()
      console.log('LIVE_MODEL_DOWNLOAD_RESULT ' + JSON.stringify({ provider: providerName, model: modelName, action, state: terminal.state, reason: terminal.reason, warning_present: true, sse_connections: streamRequests.length }))
      return
    }

    if (created.state === 'queued' || created.state === 'running') {
      await expect.poll(() => streamRequests.length, { timeout: 20_000, message: 'the browser must open the actual job SSE endpoint' }).toBeGreaterThan(0)
      const running = await waitForState(page, created.id, ['running'], 30_000)
      expect(['queued', 'running']).toContain(running.state)
      const streamsBeforeReload = streamRequests.length
      await page.reload()
      failOnFatalPageErrors()
      const reattached = await waitForState(page, created.id, ['running', 'done', 'error'], 30_000)
      if (action === 'cancel' && reattached.state !== 'running') {
        throw new Error(`The real model completed as ${reattached.state} before the reloaded Cancel control could be exercised.`)
      }
      if (reattached.state !== 'running') {
        throw new Error(`The real model completed as ${reattached.state} before the row could prove progress and cancellation after reload.`)
      }
      if (action === 'repair') {
        const label = USE_CASE_LABELS[useCase]
        const useCaseDisclosure = page.getByRole('button', { name: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`) })
        await expect(useCaseDisclosure, 'the real Settings Models use-case row must return after reload').toBeVisible({ timeout: 30_000 })
        failOnFatalPageErrors()
        await useCaseDisclosure.click({ timeout: 30_000 })
        failOnFatalPageErrors()
        await expect.poll(() => streamRequests.length, { timeout: 20_000, message: 'opening the Models row after reload must reattach to the real job SSE stream' }).toBeGreaterThan(streamsBeforeReload)
        const repairRow = page.getByTestId('model-repair')
        await expect(repairRow, 'after reload the pressed Models row must retain its real repair job').toBeVisible()
        await expect(repairRow).toContainText(/Repair queued|Downloading /)
        await expect(repairRow.getByRole('button', { name: 'Cancel the model download', exact: true })).toBeVisible()
      } else {
        await expect.poll(() => streamRequests.length, { timeout: 20_000, message: 'after reload the provider panel must reattach to the real job SSE stream' }).toBeGreaterThan(streamsBeforeReload)
        const cancelButton = page.getByRole('button', { name: `Cancel ${modelName}`, exact: true })
        await expect(cancelButton, 'after reload the provider row must retain its real download job and Cancel control').toBeVisible()
        await expect(cancelButton.locator('xpath=../..')).toContainText(/downloading/i)
      }
      if (action === 'cancel' && reattached.state === 'running') {
        const cancelLabel = 'Cancel ' + modelName
        const cancel = page.getByRole('button', { name: cancelLabel, exact: true })
        await expect(cancel).toBeVisible()
        const deleted = page.waitForResponse((response) =>
          response.url().includes(`/api/models/downloads/${created.id}`) && response.request().method() === 'DELETE')
        await cancel.click()
        const deleteResponse = await deleted
        expect(deleteResponse.status(), 'the visible Cancel action must invoke the real gateway DELETE').toBe(200)
        await waitForJobAbsence(page, created.id, 30_000)
        await expect(page.getByRole('button', { name: `Download ${modelName}`, exact: true }),
          'after the real DELETE removes the job, its provider row must return to the Download action').toBeVisible()
        console.log('LIVE_MODEL_DOWNLOAD_RESULT ' + JSON.stringify({ provider: providerName, model: modelName, action, delete_status: deleteResponse.status(), job_absent: true, sse_connections: streamRequests.length }))
        const after = await browserJson<{ providers?: ProviderRow[] }>(page, '/api/models/available')
        const current = after.value.providers?.find((row) => row.name === providerName)?.models?.find((row) => row.name === modelName)
        expect(current?.downloaded, 'cancel must leave the absent model absent').toBe(false)
        return
      }
    }

    const terminal = created.state === 'done' || created.state === 'error'
      ? created
      : await waitForState(page, created.id, ['done', 'error'], 12 * 60 * 1000)
    if (terminal.warning) {
      const warning = page.getByText(terminal.warning, { exact: false })
      await expect(warning, 'a disk-precheck warning returned by the real job must remain visible in its row after reload').toBeVisible()
    }
    if (terminal.state === 'error') {
      if (action === 'repair') await expect(page.getByRole('alert').filter({ hasText: terminal.error || /failed/i })).toBeVisible()
      else await expect(page.getByText(terminal.error || /download failed/i, { exact: false })).toBeVisible()
      throw new Error(`The real ${action} job failed and the UI surfaced its error: ${terminal.error || 'no error detail'}`)
    }
    expect(terminal.state, 'the real provider must finish the requested artifact operation').toBe('done')
    console.log('LIVE_MODEL_DOWNLOAD_RESULT ' + JSON.stringify({ provider: providerName, model: modelName, action, state: terminal.state, downloaded_bytes: terminal.downloaded_bytes, total_bytes: terminal.total_bytes, sse_connections: streamRequests.length, warning_present: !!terminal.warning }))

    const after = await browserJson<{ providers?: ProviderRow[] }>(page, '/api/models/available')
    expect(after.status).toBe(200)
    const afterModel = after.value.providers?.find((row) => row.name === providerName)?.models?.find((row) => row.name === modelName)
    expect(afterModel?.downloaded, 'successful Download/Repair must be reflected by the real catalog').toBe(true)
    if (action === 'repair') expect(afterModel?.integrity, 'successful Repair must clear truncated integrity in the real catalog').not.toBe('truncated')
  })
})
