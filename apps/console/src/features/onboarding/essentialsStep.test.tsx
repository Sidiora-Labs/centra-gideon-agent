import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import type { AppCatalogEntry, SearchCapabilitiesInfo, SearchProviderInfo, ToolItem } from '../../shared/data/api'


const previewApp = vi.fn()
const commitApp = vi.fn()
const onboardingModelCheck = vi.fn()
let boundChat = false
const appCatalog = vi.fn()
const modelProviderTypes = vi.fn()
const createModelProvider = vi.fn()
const updateModelProvider = vi.fn()
const testModelProvider = vi.fn()
const chatModels = vi.fn()
const setActiveModel = vi.fn()
const saveOnboardingState = vi.fn()

vi.mock('../../shared/data/api', () => ({
  api: {
    previewApp: (...a: unknown[]) => previewApp(...a),
    commitApp: (...a: unknown[]) => commitApp(...a),
    modelDownloads: () => Promise.resolve([]),
    modelsAvailable: () => Promise.resolve([]),
    detectLocalModel: () => Promise.resolve({ detected: false }),
    modelProviders: () => Promise.resolve([]),
    activeModels: () => Promise.resolve({ use_cases: { chat: [] }, revisions: { chat: 1 } }),
    onboardingModelCheck: () => onboardingModelCheck(),
    searchProviders: () => Promise.resolve([]),
    searchActive: () => Promise.resolve({}),
    tools: () => Promise.resolve([]),
    appCatalog: () => appCatalog(),
    modelProviderTypes: () => modelProviderTypes(),
    createModelProvider: (...a: unknown[]) => createModelProvider(...a),
    updateModelProvider: (...a: unknown[]) => updateModelProvider(...a),
    testModelProvider: (...a: unknown[]) => testModelProvider(...a),
    chatModels: () => chatModels(),
    setActiveModel: (...a: unknown[]) => setActiveModel(...a),
    saveOnboardingState: (...a: unknown[]) => saveOnboardingState(...a),
  },
}))
vi.mock('../../app/shell/appSdk', () => ({ launchChat: vi.fn(), notify: vi.fn() }))

import { EssentialsStep, laneOf, candidatesByLane, emptyEssentialGuidance, CATALOG_FAILURE_GUIDANCE, evaluateWebSearchReadiness } from './EssentialsStep'
import { invalidateKeys } from '../../shared/data/data'

function entry(over: Partial<AppCatalogEntry> & { name: string }): AppCatalogEntry {
  return {
    displayName: over.name, description: 'desc', version: '1.0.0',
    icon: '', author: 'Gideon', source: `/apps/${over.name}`, sourceKind: 'local',
    isProvider: true, providerType: 'model', tags: [], providerCapabilities: ['chat'],
    permissions: {}, crons: [], ...over,
  }
}

const OPENAI = entry({
  name: 'openai-models', displayName: 'OpenAI', providerType: 'model',
  providerCapabilities: ['chat', 'streaming', 'embedding'],
  permissions: { api: ['/api/models'], network: true },
})
const WHISPER = entry({ name: 'faster-whisper', displayName: 'Faster Whisper', providerType: 'model', providerCapabilities: ['stt'] })
const PIPER = entry({ name: 'piper-tts', displayName: 'Piper TTS', providerType: 'model', providerCapabilities: ['tts'] })
const BRAVE = entry({
  name: 'brave-search', displayName: 'Brave Search', providerType: 'search', providerCapabilities: ['search'],
  permissions: { api: ['/api/search'], cron: true },
  crons: [{ name: 'refresh', every: 3600, agent: 'default', message: 'refresh the index' }],
})
const DISCORD = entry({ name: 'discord-channel', displayName: 'Discord', providerType: 'channel', providerCapabilities: ['messaging'] })
const EMBEDDER = entry({ name: 'sentence-transformers', providerType: 'model', providerCapabilities: ['embedding'] })

const CATALOG = { bundled: [], gitSources: [], localApps: [OPENAI, WHISPER, PIPER, BRAVE, DISCORD, EMBEDDER], remoteApps: [], gitApps: [] }

const FRESH = { needs_model: true, has_model_provider: false, has_chat_binding: false }

function renderStep(over: Partial<Parameters<typeof EssentialsStep>[0]> = {}) {
  if (over.readiness?.has_chat_binding) boundChat = true
  const onDone = vi.fn(), onSkip = vi.fn(), onProgress = vi.fn()
  const r = render(<EssentialsStep readiness={FRESH} onDone={onDone} onSkip={onSkip} onProgress={onProgress} {...over} />)
  return { ...r, onDone, onSkip, onProgress }
}

const CARD = { openai: 0, brave: 1, whisper: 2, piper: 3, discord: 4 } as const

async function openCard(which: keyof typeof CARD) {
  const reviews = await screen.findAllByRole('button', { name: /^Review$/ })
  fireEvent.click(reviews[CARD[which]])
}

beforeEach(() => {
  vi.clearAllMocks()
  boundChat = false
  onboardingModelCheck.mockImplementation(async () => boundChat
    ? { ok: true, source: 'binding', bound: ['openai:gpt-5'], provider: 'openai', model: 'gpt-5', local: false }
    : { ok: false, code: 'no_binding', what: 'No chat model', why: 'Nothing is bound', fix: 'Choose a model' })
  previewApp.mockImplementation(async (name: string) => ({ ok: true, name, review_digest: 'review-1', needs_consent: false, scan: null }))
  for (const k of ['onboarding:essentials-catalog', 'onboarding:provider-types', 'onboarding:chat-models']) invalidateKeys(k)
  try { sessionStorage.clear() } catch {   }
  appCatalog.mockResolvedValue(CATALOG)
  modelProviderTypes.mockResolvedValue([{
    type: 'openai', label: 'OpenAI', app: 'openai-models', capabilities: ['chat'], multiInstance: true,
    settingsSchema: { properties: { api_key: { type: 'string', default: '', 'x-meta': { label: 'OpenAI API Key', sensitive: true } } }, required: ['api_key'] },
  }])
  createModelProvider.mockResolvedValue({ ok: true, name: 'openai' })
  testModelProvider.mockResolvedValue({ ok: true, message: 'Reachable' })
  chatModels.mockResolvedValue([{ name: 'openai/gpt-5', model_id: 'gpt-5', provider: 'openai' }])
  setActiveModel.mockImplementation(async () => { boundChat = true; return { ok: true } })
  saveOnboardingState.mockResolvedValue({ ok: true, state: {} })
  commitApp.mockResolvedValue({ ok: true, name: 'openai-models', error: '', needs_consent: false, scan: null })
})


describe('lane classification reads declared capabilities, not providerType alone', () => {
  it('keeps a speech-only model app OUT of the chat-model lane', () => {
    expect(laneOf(WHISPER)).toBe('speech')
    expect(laneOf(PIPER)).toBe('speech')
    expect(laneOf(OPENAI)).toBe('model')
  })

  it('offers no lane to a model app that can neither chat nor speak', () => {
    expect(laneOf(EMBEDDER)).toBeNull()
  })

  it('routes search and channel apps by their provider type', () => {
    expect(laneOf(BRAVE)).toBe('search')
    expect(laneOf(DISCORD)).toBe('channel')
  })

  it('pools every catalog array, so a first-party source surfaces however it arrived', () => {
    const lanes = candidatesByLane({ bundled: [OPENAI], gitSources: [], localApps: [OPENAI], gitApps: [BRAVE], remoteApps: [DISCORD] })
    expect(lanes.model.map((e) => e.name)).toEqual(['openai-models'])
    expect(lanes.search.map((e) => e.name)).toEqual(['brave-search'])
    expect(lanes.channel.map((e) => e.name)).toEqual(['discord-channel'])
  })
})


describe('nothing installs without an explicit click', () => {
  it('fires no install request on mount', async () => {
    const { onProgress } = renderStep()
    await screen.findByText('OpenAI')
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
    expect(commitApp, 'mounting the step must not install anything').not.toHaveBeenCalled()
    expect(onProgress, 'nor record an app the user never chose').not.toHaveBeenCalled()
  })

  it('fires no install request when a card\'s disclosure is opened', async () => {
    renderStep()
    await openCard('openai')
    await screen.findByText('Permissions the gateway enforces')
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
    expect(commitApp, 'reviewing an app is not consenting to install it').not.toHaveBeenCalled()
  })

  it('installs exactly one app, once, when its own Install button is clicked', async () => {
    renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    await waitFor(() => expect(commitApp).toHaveBeenCalledTimes(1))
    expect(previewApp).toHaveBeenCalledWith('openai-models', '/apps/openai-models', undefined)
    expect(commitApp).toHaveBeenCalledWith('openai-models', '/apps/openai-models', 'review-1', undefined)
  })

  it('leaves the resume-point write to the flow shell', async () => {
    renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
    expect(saveOnboardingState).not.toHaveBeenCalled()
  })
})


describe('per-app install consent is preserved', () => {
  it('discloses the enforced permissions with the Store\'s own wording', async () => {
    renderStep()
    await openCard('openai')
    expect(await screen.findByText('Permissions the gateway enforces')).toBeTruthy()
    expect(screen.getByText(/API: \/api\/models/)).toBeTruthy()
    expect(screen.getByText(/Network access: declared/)).toBeTruthy()
    expect(screen.getByText(/advisory only/)).toBeTruthy()
    expect(screen.getByText(/behind the security scanner/)).toBeTruthy()
  })

  it('discloses the recurring jobs an app will run before it is installed', async () => {
    renderStep()
    await openCard('brave')
    expect(await screen.findByText('Scheduled jobs')).toBeTruthy()
    expect(screen.getByText(/every hour/)).toBeTruthy()
    expect(commitApp).not.toHaveBeenCalled()
  })

  it('routes a scanner WARNING through the Store consent modal and re-attempts only on confirm', async () => {
    previewApp.mockResolvedValueOnce({
      ok: false, name: 'openai-models', error: '', needs_consent: true, review_digest: 'review-warning',
      scan: { verdict: 'warning', findings: [{ surface: 'py', severity: 'medium', rule: 'subprocess', path: 'p.py', evidence: 'run()' }] },
    })
    const { onProgress } = renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    const anyway = await screen.findByRole('button', { name: /Install anyway/ })
    expect(previewApp).toHaveBeenCalledTimes(1)
    expect(commitApp).not.toHaveBeenCalled()
    expect(onProgress, 'a blocked install records no progress').not.toHaveBeenCalled()
    fireEvent.click(anyway)
    await waitFor(() => expect(commitApp).toHaveBeenCalledTimes(1))
    expect(commitApp).toHaveBeenLastCalledWith('openai-models', '/apps/openai-models', 'review-warning', undefined)
  })
})


describe('the model lane completes entirely in-flow', () => {
  async function walkModelLane() {
    const h = renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    const key = await screen.findByLabelText('OpenAI API Key')
    fireEvent.change(key, { target: { value: 'sk-secret' } })
    fireEvent.click(screen.getByRole('button', { name: /Save and test/ }))
    return h
  }

  it('creates the provider with the schema-declared key, then Tests it', async () => {
    await walkModelLane()
    await waitFor(() => expect(testModelProvider).toHaveBeenCalledWith('openai'))
    expect(createModelProvider).toHaveBeenCalledWith({ name: 'openai', type: 'openai', model: '', options: { api_key: 'sk-secret' } })
  })

  it('binds the chosen chat model as a canonical provider:model ref', async () => {
    await walkModelLane()
    fireEvent.click(await screen.findByRole('button', { name: /gpt-5/ }))
    await waitFor(() => expect(setActiveModel).toHaveBeenCalledWith('chat', ['openai:gpt-5'], 1))
  })

  it('shows a failed Test inline and lets the user retry in place', async () => {
    testModelProvider.mockResolvedValue({ ok: false, message: 'invalid_api_key' })
    await walkModelLane()
    const alert = await screen.findByText('invalid_api_key')
    expect(alert.textContent).toContain('invalid_api_key')
    expect(screen.getByLabelText('OpenAI API Key')).toBeTruthy()
    expect(chatModels, 'a failed Test must not advance to binding').not.toHaveBeenCalled()
  })

  it('never puts a submitted key in an error message', async () => {
    testModelProvider.mockResolvedValue({ ok: false, message: 'invalid_api_key' })
    const { container } = await walkModelLane()
    await screen.findByText('invalid_api_key')
    expect(container.textContent).not.toContain('sk-secret')
  })

  it('skips straight to binding when a provider already exists but nothing is bound', async () => {
    renderStep({ readiness: { needs_model: true, has_model_provider: true, has_chat_binding: false } })
    await screen.findByRole('button', { name: /gpt-5/ })
    expect(commitApp, 'an existing provider needs no app install').not.toHaveBeenCalled()
    expect(createModelProvider).not.toHaveBeenCalled()
  })

  it('asks for nothing when chat already resolves', async () => {
    renderStep({ readiness: { needs_model: false, has_model_provider: true, has_chat_binding: true } })
    expect(await screen.findByText(/Chat model: openai:gpt-5/)).toBeTruthy()
    expect(onboardingModelCheck).toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /gpt-5/ })).toBeNull()
    expect(setActiveModel).not.toHaveBeenCalled()
    expect(createModelProvider).not.toHaveBeenCalled()
  })

  it('applies a corrected key to the existing instance instead of dead-ending on 409', async () => {
    createModelProvider.mockRejectedValue(new Error(JSON.stringify({ error: "Provider 'openai' already exists" })))
    await walkModelLane()
    await waitFor(() => expect(updateModelProvider).toHaveBeenCalledWith('openai', { options: { api_key: 'sk-secret' } }))
    expect(testModelProvider).toHaveBeenCalledWith('openai')
  })
})


describe('each lane records only its own progress field', () => {
  it('records the model app by name the moment it installs', async () => {
    const { onProgress } = renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    await waitFor(() => expect(onProgress).toHaveBeenCalledWith({ essentials: { model: 'openai-models' } }))
  })

  it('records a search install as a flag, naming no other lane', async () => {
  commitApp.mockResolvedValue({ ok: true, name: 'brave-search', error: '', needs_consent: false, scan: null })
    const { onProgress } = renderStep()
    await openCard('brave')
    fireEvent.click(await screen.findByRole('button', { name: /Install Brave Search/ }))
    await waitFor(() => expect(onProgress).toHaveBeenCalledWith({ essentials: { search: true } }))
    for (const [patch] of onProgress.mock.calls) expect(Object.keys(patch.essentials)).toEqual(['search'])
  })

  it('records a speech install as a flag', async () => {
  commitApp.mockResolvedValue({ ok: true, name: 'faster-whisper', error: '', needs_consent: false, scan: null })
    const { onProgress } = renderStep()
    await openCard('whisper')
    fireEvent.click(await screen.findByRole('button', { name: /Install Faster Whisper/ }))
    await waitFor(() => expect(onProgress).toHaveBeenCalledWith({ essentials: { speech: true } }))
  })

  it('records a channel install by app name', async () => {
  commitApp.mockResolvedValue({ ok: true, name: 'discord-channel', error: '', needs_consent: false, scan: null })
    const { onProgress } = renderStep()
    await openCard('discord')
    fireEvent.click(await screen.findByRole('button', { name: /Install Discord/ }))
    await waitFor(() => expect(onProgress).toHaveBeenCalledWith({ essentials: { channel: 'discord-channel' } }))
  })
})


describe('skipping every optional lane still reaches the next step', () => {
  it('Continue is unavailable until the model lane resolves, then advances', async () => {
    const { onDone, onProgress } = renderStep()
    const cont = await screen.findByRole('button', { name: /Continue/ })
    fireEvent.click(cont)
    expect(onDone, 'the required rail is not yet satisfied').not.toHaveBeenCalled()

    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    fireEvent.change(await screen.findByLabelText('OpenAI API Key'), { target: { value: 'sk-secret' } })
    fireEvent.click(screen.getByRole('button', { name: /Save and test/ }))
    fireEvent.click(await screen.findByRole('button', { name: /gpt-5/ }))

    await waitFor(() => expect(screen.getByRole('button', { name: /Continue/ })).not.toHaveAttribute('aria-disabled'))
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
    expect(onDone).toHaveBeenCalledWith('openai:gpt-5')
    expect(commitApp).toHaveBeenCalledTimes(1)
    const named = onProgress.mock.calls.flatMap(([p]) => Object.keys(p.essentials ?? {}))
    expect(named).toEqual(['model'])
  })

  it('offers a "Set up later" escape so the step never traps a user', async () => {
    const { onSkip } = renderStep()
    fireEvent.click(await screen.findByRole('button', { name: /Set up later/ }))
    expect(onSkip).toHaveBeenCalled()
  })
})


describe('a failed catalog fetch says so', () => {
  it('announces the load failure and offers a retry instead of "no apps"', async () => {
    appCatalog.mockRejectedValue(new Error('gateway unreachable'))
    renderStep()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/app catalog/i)
    expect(screen.queryByText(/No model provider app is available/)).toBeNull()
    expect(screen.getByRole('button', { name: /Retry|Try again/i })).toBeTruthy()
  })

  it('empty guidance names the offline download only when the offer is visible', () => {
    expect(emptyEssentialGuidance('model', true)).toContain('Download the small offline model above')
    expect(emptyEssentialGuidance('model', false)).not.toMatch(/download|offline/i)
    expect(emptyEssentialGuidance('model', false)).toContain('Settings → Models')
  })

  it('empty guidance gives available actions without repository implementation language', () => {
    for (const lane of ['search', 'speech', 'channel'] as const) {
      const copy = emptyEssentialGuidance(lane, false)
      expect(copy).toContain('Store')
      expect(copy).toContain('Settings or Connections')
      expect(copy).not.toMatch(/first.party|repository|workspace|dev tree/i)
    }
    expect(CATALOG_FAILURE_GUIDANCE).toContain('Retry')
    expect(CATALOG_FAILURE_GUIDANCE).not.toMatch(/first.party|repository|workspace|dev tree/i)
  })
})

describe('web search readiness uses the configured provider and current tool state', () => {
  it('shows Web search ready only when web_search is usable', () => {
    const capabilities: SearchCapabilitiesInfo = {
      returns_content: true, returns_answer: true, returns_highlights: false,
      supports_recency: false, supports_domains: false, supports_fetch: false, depths: [],
    }
    const provider: SearchProviderInfo = {
      name: 'searxng', display_name: 'SearXNG', capabilities, available: true,
    }
    const webSearch: ToolItem = {
      name: 'web_search', description: 'Search the web', provider: 'core',
      disabled: false, providerDisabled: false,
    }

    expect(evaluateWebSearchReadiness([provider], [webSearch])).toBe('ready')
    expect(evaluateWebSearchReadiness([{ ...provider, available: false }], [webSearch])).toBe('not-ready')
    expect(evaluateWebSearchReadiness([provider], [{ ...webSearch, disabled: true }])).toBe('not-ready')
    expect(evaluateWebSearchReadiness([provider], [{ ...webSearch, providerDisabled: true }])).toBe('not-ready')
    expect(evaluateWebSearchReadiness([provider], [])).toBe('not-ready')
    expect(evaluateWebSearchReadiness(undefined, [webSearch])).toBe('unknown')
    expect(evaluateWebSearchReadiness([provider], undefined)).toBe('unknown')
    expect(evaluateWebSearchReadiness([provider], [webSearch], true)).toBe('unknown')
  })
})
