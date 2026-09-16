import { useMemo, useReducer, useRef, useState } from 'react'
import { api, type AppCatalogEntry, type ChatModelOption, type ModelProviderType, type OnboardingState, type OnboardingStatePatch } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { guardedFromApp, useGuardedInstall } from '../../shared/data/useGuardedInstall'
import { catalogApps } from '../../shared/data/appCatalog'

export type EssentialLane = 'model' | 'search' | 'speech' | 'channel'
export type ModelPhase = 'pick' | 'configure' | 'bind' | 'done'
export function essentialLane(entry: AppCatalogEntry): EssentialLane | null {
  switch (entry.providerType) {
    case 'search': return 'search'
    case 'channel': return 'channel'
    case 'model': {
      const capabilities = new Set(entry.providerCapabilities ?? [])
      if (capabilities.has('chat')) return 'model'
      return ['stt', 'tts'].some(capability => capabilities.has(capability)) ? 'speech' : null
    }
    default: return null
  }
}
export function essentialCandidates(catalog: Awaited<ReturnType<typeof api.appCatalog>> | undefined) {
  const lanes: Record<EssentialLane, AppCatalogEntry[]> = { model: [], search: [], speech: [], channel: [] }
  for (const entry of catalogApps(catalog)) {
    const lane = entry?.name ? essentialLane(entry) : null
    if (lane) lanes[lane].push(entry)
  }
  Object.values(lanes).forEach(entries => entries.sort((a, b) => (a.displayName || a.name).localeCompare(b.displayName || b.name)))
  return lanes
}
type SetupState = { installed: Record<string, true>; open: string; expanded: Record<string, true>; modelApp: string; phase: ModelPhase; boundLabel: string }
export function useEssentialSetup(readiness: OnboardingState | null, onProgress: (patch: OnboardingStatePatch) => void) {
  const { data: catalog, error: catalogError, refresh } = useQuery('onboarding:essentials-catalog', () => api.appCatalog())
  const [state, change] = useReducer((state: SetupState, patch: Partial<SetupState>) => ({ ...state, ...patch }), {
    installed: {}, open: '', expanded: {}, modelApp: '', boundLabel: '',
    phase: readiness && !readiness.needs_model ? 'done' : readiness?.has_model_provider ? 'bind' : 'pick',
  } as SetupState)
  const pendingRef = useRef<AppCatalogEntry | null>(null)
  const active = useRef(false)
  const guarded = useGuardedInstall(confirm => api.installApp(pendingRef.current?.pointer || pendingRef.current?.source || '', confirm).then(guardedFromApp))
  const record = (entry: AppCatalogEntry, lane: EssentialLane) => {
    change({ installed: { ...state.installed, [entry.name]: true }, open: '', ...(lane === 'model' ? { modelApp: entry.name, phase: 'configure' as const } : {}) })
    const progress = { model: { model: entry.name }, search: { search: true }, speech: { speech: true }, channel: { channel: entry.name } }
    onProgress({ essentials: progress[lane] })
  }
  const attempt = async (entry: AppCatalogEntry, confirm: boolean) => {
    if (active.current) return
    active.current = true
    pendingRef.current = entry
    try {
      if (!confirm) guarded.reset()
      const result = await (confirm ? guarded.confirmInstall() : guarded.install())
      const lane = essentialLane(entry)
      if (result?.ok && lane) record(entry, lane)
    } finally { active.current = false }
  }
  return { ...state, catalog, catalogError, refresh, lanes: useMemo(() => essentialCandidates(catalog), [catalog]), guarded, pendingRef,
    install: (entry: AppCatalogEntry) => attempt(entry, false),
    confirmInstall: () => { if (pendingRef.current) return attempt(pendingRef.current, true) },
    toggle: (name: string) => { change({ open: state.open === name ? '' : name }); guarded.reset() },
    expand: (lane: EssentialLane) => change({ expanded: { ...state.expanded, [lane]: true } }),
    configured: () => change({ phase: 'bind' }), bound: (label: string) => change({ phase: 'done', boundLabel: label }),
  }
}
export function setupErrorText(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error ?? '')
  try { const parsed = JSON.parse(text); return String(parsed?.error ?? text) } catch { return text }
}
export function useProviderConfiguration(app: string, onConfigured: () => void) {
  const { data: types, error: typesError, refresh } = useQuery('onboarding:provider-types', () => api.modelProviderTypes())
  const provider: ModelProviderType | undefined = types?.find(entry => entry.app === app)
  const props = provider?.settingsSchema?.properties ?? {}
  const defaults = Object.fromEntries(Object.entries(props).map(([key, field]) => [key, String(field.default ?? '')]))
  const [draft, setDraft] = useState<{ type: string; values: Record<string, string> }>({ type: '', values: {} })
  const values = draft.type === provider?.type ? draft.values : defaults
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const active = useRef(false)
  const submit = async () => {
    if (!provider || active.current) return
    const missing = (provider.settingsSchema?.required ?? []).find(key => !String(values[key] ?? props[key]?.default ?? '').trim())
    if (missing) { setError(`${props[missing]?.['x-meta']?.label || missing} is required`); return }
    const options = Object.fromEntries(Object.entries(props).flatMap(([key, field]) => {
      const value = (values[key] ?? String(field.default ?? '')).trim()
      return value ? [[key, value]] : []
    }))
    active.current = true; setBusy(true); setError('')
    try {
      try { await api.createModelProvider({ name: provider.type, type: provider.type, model: '', options }) }
      catch (failure) {
        if (!/already exists/i.test(setupErrorText(failure))) throw failure
        await api.updateModelProvider(provider.type, { options })
      }
      const result = await api.testModelProvider(provider.type)
      if (result.ok) onConfigured()
      else setError(result.message || 'The provider test failed.')
    } catch (failure) { setError(setupErrorText(failure) || 'Could not save the provider.') }
    finally { active.current = false; setBusy(false) }
  }
  return { types, typesError, refresh, t: provider, props, values, error, busy, submit,
    setValue: (key: string, value: string) => setDraft({ type: provider?.type ?? '', values: { ...values, [key]: value } }),
  }
}
export function useChatModelBinding(onBound: (label: string) => void) {
  const { data: models, error, refresh } = useQuery('onboarding:chat-models', () => api.chatModels())
  const [binding, setBinding] = useState('')
  const [failed, setFailed] = useState('')
  const active = useRef(false)
  const bind = async (model: ChatModelOption) => {
    if (active.current) return
    active.current = true; setBinding(model.name); setFailed('')
    try {
      const reference = model.provider ? `${model.provider}:${model.model_id}` : model.model_id
      await api.setActiveModel('chat', [reference]); onBound(model.model_id)
    } catch (failure) { setBinding(''); setFailed(setupErrorText(failure) || 'Could not bind that model.') }
    finally { active.current = false }
  }
  return { models, error, refresh, binding, failed, bind }
}
