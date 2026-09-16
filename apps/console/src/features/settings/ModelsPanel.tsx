import { useEffect, useMemo, useState } from 'react'
import { ResultAnnouncement } from '../../shared/ui/ListControls'
import {
  Check, MessageSquare, Boxes, Mic, Volume2, Eye, ImagePlus,
  Ear, Music, ScanEye, Clapperboard, Users, Download, Code2, BrainCircuit,
  Moon, Network, RefreshCcw, ArrowUp, ArrowDown, X, AlertTriangle, Wrench,
  Trash2, Gavel, type LucideIcon,
} from 'lucide-react'
import { api, type AvailableModel, type JudgeBenchRecommendation, type ProviderHealth } from '../../shared/data/api'
import { humanBytes } from '../../shared/data/chunkedUpload'
import {
  occupantDetail, pressureDetail, pressureTone, reclaimableCount, sortOccupants,
} from '../../shared/data/residency'
import { IconButton } from '../../shared/ui/IconButton'
import { Button } from '../../shared/ui/Button'
import { Meter } from '../../shared/ui/Meter'
import { WavyProgress } from '../../shared/ui/WavyProgress'
import { SearchField } from '../../shared/ui/SearchField'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { confirm } from '../../shared/ui/dialog'
import { PanelHeader, Section, RowGroup, ToggleRow } from './settingsUI'
import { notify } from '../../app/shell/appSdk'
import { FormSkeleton, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { fvs } from '../../shared/theme/fontWeight'
import { accentChip } from '../../shared/theme/accent'
import { DisclosureCard } from '../../shared/ui/DisclosureCard'
import { BUSY_REASON } from '../../shared/ui/unavailable'
import { reportingWrite } from '../../app/shell/reportingWrite'

const USE_CASE_META: Record<string, { label: string; group?: string; description: string; chain: boolean; icon: LucideIcon; fallback?: string }> = {
  chat: { label: 'Chat', description: 'Conversational models for chat and agent interactions. Order matters: the first model is the default; later ones are fallbacks used when an earlier provider is down.', chain: true, icon: MessageSquare },
  code_tools: { label: 'Code & tools', group: 'Chat routing', description: 'Native agent turns that lean on tool use and code work.', chain: true, icon: Code2, fallback: 'Chat' },
  reasoning: { label: 'Reasoning', group: 'Chat routing', description: 'One-shot judgment calls — web-page extraction and other guarded single completions.', chain: true, icon: BrainCircuit, fallback: 'Chat' },
  background: { label: 'Background', group: 'Chat routing', description: 'Housekeeping chores — session titles, tags, suggestions, digests, consolidation. Bind a cheap or local model here so chores stop burning your main chat model.', chain: true, icon: Moon, fallback: 'Chat' },
  orchestration: { label: 'Orchestration', group: 'Chat routing', description: 'Supervising turns and subagents spawned without an explicit model.', chain: true, icon: Network, fallback: 'Chat' },
  loops: { label: 'Loops', group: 'Chat routing', description: 'Autonomous goal-loop workers, gates and judges — long-horizon work that benefits from a long-context model.', chain: true, icon: RefreshCcw, fallback: 'Chat' },
  embedding: { label: 'Embedding', group: 'Capabilities', description: 'Vector embedding models for knowledge and memory.', chain: false, icon: Boxes },
  stt: { label: 'Speech-to-text', group: 'Capabilities', description: 'Voice transcription models.', chain: false, icon: Mic },
  tts: { label: 'Text-to-speech', group: 'Capabilities', description: 'Voice synthesis models.', chain: false, icon: Volume2 },
  diarization: { label: 'Speaker diarization', group: 'Capabilities', description: 'Labels "who spoke when" in audio/video (speaker turns). Served by diarization providers (ONNX, pyannote).', chain: false, icon: Users },
  image_modality: { label: 'Image · Modality', group: 'Image', description: 'Models that understand images as input (vision / VLM).', chain: true, icon: Eye },
  image_gen: { label: 'Image · Generation', group: 'Image', description: 'Models that generate images from a prompt.', chain: false, icon: ImagePlus },
  audio_modality: { label: 'Audio · Modality', group: 'Audio', description: 'Models that understand audio as input.', chain: false, icon: Ear },
  audio_gen: { label: 'Audio · Generation', group: 'Audio', description: 'Models that generate audio, music, or sound effects.', chain: false, icon: Music },
  video_modality: { label: 'Video · Modality', group: 'Video', description: 'Models that understand video as input.', chain: false, icon: ScanEye },
  video_gen: { label: 'Video · Generation', group: 'Video', description: 'Models that generate video from a prompt.', chain: false, icon: Clapperboard },
}
const USE_CASE_ORDER = [
  'chat', 'code_tools', 'reasoning', 'background', 'orchestration', 'loops',
  'embedding', 'stt', 'tts', 'diarization',
  'image_modality', 'image_gen', 'audio_modality', 'audio_gen', 'video_modality', 'video_gen',
]

const CHAT_SUBCATEGORIES = new Set(['code_tools', 'reasoning', 'background', 'orchestration', 'loops'])

export function capableModels(useCase: string, allModels: AvailableModel[], activeModels: string[]): AvailableModel[] {
  const capability = CHAT_SUBCATEGORIES.has(useCase) ? 'chat' : useCase
  const seen = new Set<string>()
  const out: AvailableModel[] = []
  for (const m of allModels) {
    if (!m.capabilities.includes(capability)) continue
    const ref = `${m.provider}:${m.id}`
    if (seen.has(ref)) continue
    seen.add(ref)
    out.push(m)
  }
  for (const ref of activeModels) {
    if (seen.has(ref)) continue
    seen.add(ref)
    const sep = ref.indexOf(':')
    const provider = sep >= 0 ? ref.slice(0, sep) : ''
    const id = sep >= 0 ? ref.slice(sep + 1) : ref
    out.push({ id, name: id, provider, capabilities: [useCase], downloaded: false } as AvailableModel)
  }
  return out
}

/** The contract chips a model row shows (LMMV §2.2/§2.3), as pure data so the mapping
 *  is unit-testable independently of rendering:
 *   - `deprecated`/`sunset` status → an informational chip (the model stays bindable).
 *   - a non-commercial license → a warning chip surfaced AT BIND TIME (Success Criterion 7).
 *   - `integrity: "truncated"` → a danger chip whose row offers Repair (re-download).
 *  A hosted/remote model (no catalog fields) yields no chips. */
export type ChipKind = 'status' | 'non-commercial' | 'truncated'
export function modelChips(m: AvailableModel): ChipKind[] {
  const chips: ChipKind[] = []
  if (m.status === 'deprecated' || m.status === 'sunset') chips.push('status')
  if (m.non_commercial) chips.push('non-commercial')
  if (m.integrity === 'truncated') chips.push('truncated')
  return chips
}

function ModelChips({ model, onRepair, repairing }: {
  model: AvailableModel; onRepair: () => void; repairing: boolean
}) {
  const chips = modelChips(model)
  if (chips.length === 0) return null
  return (
    <span className="flex shrink-0 items-center gap-1">
      {model.status === 'deprecated' && (
        <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low uppercase tracking-wide"
          title="Deprecated — still bindable, but a newer model is preferred.">deprecated</span>
      )}
      {model.status === 'sunset' && (
        <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low uppercase tracking-wide"
          title="Sunset — hidden from new bindings; an existing binding keeps working.">sunset</span>
      )}
      {model.non_commercial && (
        <span data-type="caption" className="inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5"
          style={{ background: 'color-mix(in srgb, var(--color-warning) 16%, transparent)', color: 'var(--color-warning)' }}
          title={`Non-commercial license${model.license ? ` (${model.license})` : ''} — for personal/research use only.`}>
          <AlertTriangle size={9} /> non-commercial
        </span>
      )}
      {model.integrity === 'truncated' && (
        <>
          <span data-type="caption" className="inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5"
            style={{ background: 'color-mix(in srgb, var(--color-danger) 16%, transparent)', color: 'var(--color-danger)' }}
            title="Downloaded weights are incomplete — this model won't load. Repair to re-download.">
            truncated
          </span>
          <button type="button" onClick={onRepair} disabled={repairing}
            data-type="caption" className="inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5 transition-colors hover:bg-surface-high"
            style={{ background: 'var(--color-surface-high)', color: 'var(--color-on-surface)' }}
            title="Re-download this model's weights.">
            <Wrench size={9} /> {repairing ? 'repairing…' : 'Repair'}
          </button>
        </>
      )}
    </span>
  )
}

function ReclaimButton({ onReclaimed }: { onReclaimed: () => void }) {
  const [totalBytes, setTotalBytes] = useState(0)
  const [busy, setBusy] = useState(false)

  const refreshCandidates = () =>
    api.modelDownloadCleanupCandidates()
      .then((r) => setTotalBytes(r.total_bytes))
      .catch(() => setTotalBytes(0))

  useEffect(() => { refreshCandidates() }, [])

  if (totalBytes <= 0) return null

  const reclaim = async () => {
    const ok = await confirm({
      title: `Reclaim ${humanBytes(totalBytes)}?`,
      body: 'Deletes partial-download leftovers (.part / .tmp / .incomplete files) from cancelled or interrupted fetches. Fully downloaded models are untouched.',
      confirmLabel: 'Reclaim',
    })
    if (!ok) return
    setBusy(true)
    try {
      if (!(await reportingWrite('reclaim that space', () => api.modelDownloadCleanup()))) return
      await refreshCandidates()
      onReclaimed()
    } finally { setBusy(false) }
  }

  return (
    <Button variant="tonal" size="xs" loading={busy} onClick={reclaim}
      title="Delete partial-download leftovers from cancelled or interrupted fetches.">
      <Trash2 size={13} /> Reclaim {humanBytes(totalBytes)}
    </Button>
  )
}

export function ModelsPanel() {
  const { data, refresh } = useQuery('settings:models', async () => {
    const [rows, active] = await Promise.all([
      api.modelsAvailable().catch(() => [] as { name: string; models?: AvailableModel[] }[]),
      api.modelsActive().catch(() => ({} as Record<string, string[]>)),
    ])
    return { allModels: rows.flatMap((r) => r.models ?? []), active }
  }, { persist: true })
  const { data: health } = useQuery('settings:models-health', () =>
    api.modelsHealth().then((h) => h.providers).catch(() => [] as ProviderHealth[]), { persist: false })
  const { data: judgeRecs } = useQuery('settings:judge-bench-recs', () =>
    api.judgeBench().then((v) => v.recommendations).catch(() => [] as JudgeBenchRecommendation[]),
    { persist: false })
  const allModels = data?.allModels
  const active = data?.active ?? {}

  const reloadActive = () => { invalidateKeys('settings:models'); refresh() }

  if (!allModels) return <ListSkeleton rows={6} />

  return (
    <div>
      <div className="flex items-start justify-between gap-3">
        <PanelHeader title="Models" hint="Assign discovered models to each use case. Chat and its routing sub-categories store an ordered fallback chain — the first model is the default; later ones take over when an earlier provider is down. Modality means understanding that media as input; Generation means producing it." />
        <div className="shrink-0 pt-1"><ReclaimButton onReclaimed={reloadActive} /></div>
      </div>
      {
}
      <Section title="Model bindings" hint="One model — or an ordered fallback chain — per use case.">
        {allModels.length === 0 && (
          <div data-type="body-s" className="mb-3 rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low">
            No models discovered. Add a backend in <span className="text-on-surface">Providers</span> and test its connection.
          </div>
        )}
        {USE_CASE_ORDER.map((uc, i) => {
          const meta = USE_CASE_META[uc]
          const prevGroup = i > 0 ? USE_CASE_META[USE_CASE_ORDER[i - 1]]?.group : undefined
          const showGroupHeader = meta?.group && meta.group !== prevGroup
          return (
            <div key={uc}>
              {showGroupHeader && <div data-type="caption" className="mb-1.5 mt-3 px-1 text-on-surface-low uppercase tracking-wide">{meta.group}</div>}
              <UseCaseRow useCase={uc} activeModels={active[uc] ?? []} allModels={allModels} health={health ?? []} judgeRec={(judgeRecs ?? []).find((r) => r.verdict === 'recommended' && r.use_case === uc)} onChanged={reloadActive} />
            </div>
          )
        })}
      </Section>
      <LoadedModelsSection />
      <PromptCacheSection />
    </div>
  )
}

function LoadedModelsSection() {
  const { data, error: loadErr, refresh } = useQuery('models:loaded', () =>
    api.modelsLoaded(), { persist: false },
  )
  const [busy, setBusy] = useState('')

  if (!data && loadErr) return <LoadError what="loaded models" error={loadErr} onRetry={refresh} />
  if (!data) return <FormSkeleton sections={1} what="loaded models" />

  const rows = sortOccupants(data.loaded)
  const reclaimable = reclaimableCount(rows)
  const notReady = data.providers.filter((p) => p.state !== 'ready')

  const unload = async (provider: string) => {
    const ok = await confirm({
      title: `Unload ${provider}?`,
      body: 'Frees the memory this provider holds. The next request loads the model again, which takes as long as the first load did.',
      confirmLabel: 'Unload',
    })
    if (!ok) return
    setBusy(provider)
    try {
      await api.unloadModelProvider(provider)
      invalidateKeys('models:loaded')
      refresh()
    } catch (e) {
      notify(`Couldn't unload ${provider}: ${String((e as Error)?.message || e)}`, 'error')
    } finally {
      setBusy('')
    }
  }

  return (
    <Section
      title="On this machine"
      hint={
        reclaimable > 0
          ? `${reclaimable} resident model${reclaimable === 1 ? '' : 's'} no longer bound to a use case — unloading frees its memory until something needs it again.`
          : 'Models currently held in memory, and how much of this machine they are using.'
      }
    >
      <div className="rounded-lg bg-surface-container px-4 py-3">
        <Meter
          label="System memory in use"
          pct={data.pressure.used_pct}
          tone={pressureTone(data.pressure)}
          detail={pressureDetail(data.pressure)}
        />
        {notReady.length > 0 && (
          <ul data-type="body-s" className="mt-3 flex flex-col gap-0.5 text-on-surface-low">
            {notReady.map((p) => (
              <li key={p.provider}>
                {p.display_name}:{' '}
                {p.state === 'loading' ? 'loading a model now' : 'unavailable on this machine'}
              </li>
            ))}
          </ul>
        )}
        {rows.length === 0 ? (
          <div data-type="body-s" className="mt-3 text-on-surface-low">
            No models are loaded right now. One loads on its first use.
          </div>
        ) : (
          <div className="mt-3 flex flex-col gap-1.5">
            {rows.map((row) => (
              <div
                key={`${row.provider}:${row.model}`}
                className="flex items-center gap-2 rounded-md bg-surface-high px-2.5 py-1.5"
              >
                <span className="flex min-w-0 flex-1 flex-col">
                  <span data-type="label-s" className="truncate text-on-surface" style={fvs(500)}>
                    {row.model || row.provider}
                  </span>
                  <span data-type="caption" className="truncate text-on-surface-low">
                    {row.provider} · {occupantDetail(row)}
                  </span>
                </span>
                <Button
                  variant="tonal"
                  size="xs"
                  loading={busy === row.provider}
                  onClick={() => unload(row.provider)}
                  ariaLabel={`Unload ${row.model || row.provider}`}
                  title="Free the memory this provider holds"
                >
                  Unload
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>
    </Section>
  )
}

function PromptCacheSection() {
  const { data, error: loadErr, refresh } = useQuery('settings:models-prompt-cache', () =>
    api.gideonConfig().then((c) => (c.agent ?? {}) as Record<string, unknown>),
    { persist: true },
  )
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null)
  useEffect(() => { if (data) setCfg(data) }, [data])

  if (!data && loadErr) return <LoadError what="prompt-cache setting" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={1} what="prompt-cache setting" />

  const patch = (key: string, value: boolean, onSaved: () => void) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`agent.${key}`, value).then(onSaved).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save prompt caching: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <Section title="Prompt caching" hint="Reuse the stable part of the prompt across turns on providers that support it.">
      <RowGroup>
        <ToggleRow label="Prompt caching" cfg={cfg} field="prompt_cache_enabled" patch={patch}
          hint="Ask providers that support it to cache the stable prompt prefix, cutting cost and latency on multi-turn work. Providers without cache support are unaffected. Turn it off to rule caching out when debugging a provider — what the model is shown, and in what order, is identical either way." />
      </RowGroup>
    </Section>
  )
}

function HealthDot({ provider, health }: { provider: string; health: ProviderHealth[] }) {
  const h = health.find((p) => p.name === provider)
  if (!h) return null
  const color = h.breaker_state === 'open' ? 'var(--color-danger)'
    : h.breaker_state === 'half_open' ? 'var(--color-warning)' : 'var(--color-ok)'
  const label = h.breaker_state === 'open'
    ? `${provider}: circuit open (${h.consecutive_failures} consecutive failures) — chain entries on this provider are skipped until it recovers`
    : h.breaker_state === 'half_open' ? `${provider}: recovering — next call probes it` : `${provider}: healthy`
  return <span role="img" className="size-2 shrink-0 rounded-pill" style={{ background: color }} title={label} aria-label={label} />
}

function UseCaseRow({ useCase, activeModels, allModels, health, judgeRec, onChanged }: {
  useCase: string; activeModels: string[]; allModels: AvailableModel[]; health: ProviderHealth[]
  judgeRec?: JudgeBenchRecommendation; onChanged: () => void
}) {
  const [saving, setSaving] = useState(false)
  const [query, setQuery] = useState('')
  const [reindex, setReindex] = useState<import('../../shared/data/api').ReindexJob | null>(null)
  const [repairing, setRepairing] = useState<string | null>(null)
  const meta = USE_CASE_META[useCase] ?? { label: useCase, description: '', chain: false, icon: Boxes }
  const capable = useMemo(() => capableModels(useCase, allModels, activeModels), [allModels, useCase, activeModels])
  const matched = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return capable
    return capable.filter((m) => {
      const ref = `${m.provider}:${m.id}`
      return activeModels.includes(ref)
        || `${m.name} ${m.id} ${m.provider}`.toLowerCase().includes(q)
    })
  }, [capable, query, activeModels])
  const filtered = useMemo(() => {
    const active: typeof matched = []
    const rest: typeof matched = []
    for (const m of matched) (activeModels.includes(`${m.provider}:${m.id}`) ? active : rest).push(m)
    return active.length ? [...active, ...rest] : matched
  }, [matched, activeModels])

  const startReindex = () => {
    api.startEmbeddingReindex().then((job) => {
      setReindex(job)
      if (job.status !== 'running') return
      const es = new EventSource(api.embeddingReindexStreamUrl(job.id))
      const onFrame = (e: MessageEvent) => {
        try { const j = JSON.parse(e.data) as import('../../shared/data/api').ReindexJob; setReindex(j); if (j.status !== 'running') es.close() } catch {   }
      }
      for (const ev of ['snapshot', 'progress', 'done', 'error']) es.addEventListener(ev, onFrame as EventListener)
      es.onerror = () => {
        es.close()
        setReindex((r) => (r && r.status === 'running'
          ? { ...r, status: 'error', error: 'Lost the progress feed — the re-index may still be running in the background. Reload to check.' }
          : r))
      }
    }).catch((err) => {
      let msg = err instanceof Error ? err.message : String(err)
      try { msg = JSON.parse(msg).error || msg } catch {   }
      setReindex({ id: '', model: '', status: 'error', phase: 'error', done: 0, total: 0, knowledge: 0, memory: 0, error: msg })
    })
  }

  const setActive = async (models: string[]) => {
    if (useCase === 'embedding') {
      const ok = await confirm({
        title: 'Change the embedding model?',
        body: 'Changing the embedding model will re-index ALL knowledge and memories. Existing embeddings are computed with the current model and are incompatible with a different one, so they must be regenerated.\n\nRe-indexing runs in the background and may take a while for large stores.',
        confirmLabel: 'Change & re-index',
      })
      if (!ok) return
    }
    setSaving(true)
    try {
      if (!(await reportingWrite('change the model', () => api.setActiveModel(useCase, models)))) return
      onChanged()
      if (useCase === 'embedding' && models.length > 0) startReindex()
    } finally { setSaving(false) }
  }
  const toggle = (ref: string) => {
    if (meta.chain) setActive(activeModels.includes(ref) ? activeModels.filter((m) => m !== ref) : [...activeModels, ref])
    else setActive(activeModels.includes(ref) ? [] : [ref])
  }
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir
    if (j < 0 || j >= activeModels.length) return
    const next = [...activeModels]
    ;[next[i], next[j]] = [next[j], next[i]]
    setActive(next)
  }
  const repair = async (m: AvailableModel) => {
    const ref = `${m.provider}:${m.id}`
    setRepairing(ref)
    try {
      await api.startModelDownload(m.provider, m.id)
      onChanged()
    } catch (e) {
      notify(`Couldn't re-download ${m.id}: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setRepairing(null) }
  }

  return (
    <DisclosureCard icon={meta.icon} label={meta.label} active={activeModels.length > 0} count={capable.length}
      subtitle={activeModels.length > 0
        ? meta.chain && activeModels.length > 1
          ? `chain of ${activeModels.length}`
          : `${activeModels.length} active`
        : meta.fallback
          ? <span className="italic">uses your {meta.fallback} chain</span>
          : <span className="italic">none configured</span>}>
      {
}
      <p data-type="body-s" className="text-on-surface-low">{meta.description}</p>
      <div data-type="caption" className="inline-flex w-fit items-center gap-1.5 rounded-md px-2 py-1"
        style={meta.chain ? accentChip : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
        <span className="size-1.5 rounded-pill" style={{ background: meta.chain ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
        {meta.chain ? 'Fallback chain — first is the default, later entries take over on failure' : 'Single-select — one model per use case'}
      </div>

      {
}
      {judgeRec && judgeRec.model_ref && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg bg-surface px-2.5 py-2">
          <Gavel size={13} className="shrink-0 text-on-surface-low" />
          <span data-type="caption" className="text-on-surface-low">
            Judge benchmark: cheapest adequate tier is <span className="text-on-surface">{judgeRec.tier}</span>
            {' '}at {judgeRec.samples} sample{judgeRec.samples === 1 ? '' : 's'} — <span className="text-on-surface">{judgeRec.model_ref}</span>
          </span>
          {activeModels[0] === judgeRec.model_ref ? (
            <span data-type="caption" className="inline-flex items-center gap-1 text-on-surface-low">
              <Check size={12} /> already the default
            </span>
          ) : (
            <Button size="sm" variant="tonal" disabled={saving} disabledReason={BUSY_REASON}
              onClick={() => setActive([judgeRec.model_ref, ...activeModels.filter((m) => m !== judgeRec.model_ref)])}>
              Bind as default
            </Button>
          )}
        </div>
      )}

      {
}
      {meta.chain && activeModels.length > 0 && (
        <div className="flex flex-col gap-1 rounded-lg bg-surface p-2">
          {activeModels.map((ref, i) => {
            const sep = ref.indexOf(':')
            const provider = sep >= 0 ? ref.slice(0, sep) : ''
            const id = sep >= 0 ? ref.slice(sep + 1) : ref
            return (
              <div key={ref} className="flex items-center gap-2 rounded-md bg-surface-container px-2.5 py-1.5">
                <span data-type="caption" className="w-16 shrink-0 text-on-surface-low uppercase tracking-wide">
                  {i === 0 ? 'default' : `fallback ${i}`}
                </span>
                <HealthDot provider={provider} health={health} />
                <span data-type="body-s" className="min-w-0 flex-1 truncate font-mono text-on-surface">{id}</span>
                {provider && <span data-type="caption" className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low">{provider}</span>}
                {
}
                <IconButton icon={ArrowUp} label={`Move ${id} up`} size={24} iconSize={13}
                  disabled={i === 0} loading={saving} onClick={() => move(i, -1)}
                  disabledReason={i === 0 ? 'Already the default' : undefined} />
                <IconButton icon={ArrowDown} label={`Move ${id} down`} size={24} iconSize={13}
                  disabled={i === activeModels.length - 1} loading={saving} onClick={() => move(i, 1)}
                  disabledReason={i === activeModels.length - 1 ? 'Already the last fallback' : undefined} />
                <IconButton icon={X} label={`Remove ${id} from chain`} size={24} iconSize={13}
                  loading={saving} onClick={() => setActive(activeModels.filter((m) => m !== ref))} />
              </div>
            )
          })}
        </div>
      )}

      {useCase === 'embedding' && reindex && (
        <div data-type="caption" className="rounded-md px-3 py-2"
          style={{ background: reindex.status === 'error' ? 'color-mix(in srgb, var(--color-danger) 10%, transparent)' : 'var(--color-surface-high)' }}>
          {
}
          {reindex.status === 'error' ? (
            <span style={{ color: 'var(--color-danger)' }}>{reindex.id ? reindex.error : `Re-index not started: ${reindex.error}`}</span>
          ) : reindex.status === 'done' ? (
            <span style={{ color: 'var(--color-ok)' }}>Re-indexed {reindex.knowledge} knowledge + {reindex.memory} memory embeddings.</span>
          ) : (
            <div className="flex flex-col gap-1.5">
              <span className="text-on-surface-var">Re-indexing embeddings — {reindex.phase}{reindex.total > 0 ? ` (${reindex.done}/${reindex.total})` : '…'}</span>
              {
}
              {reindex.total > 0 ? (
                <div className="h-1.5 w-full overflow-hidden rounded-pill bg-surface-container">
                  <div className="h-full rounded-pill bg-primary transition-[width]" style={{ width: `${Math.min(100, Math.round((reindex.done / reindex.total) * 100))}%` }} />
                </div>
              ) : (
                <WavyProgress width={140} />
              )}
            </div>
          )}
        </div>
      )}

      {capable.length === 0 ? (
        <div data-type="body-s" className="rounded-lg border border-dashed border-outline-variant/50 px-3 py-3 text-on-surface-low italic">
          {meta.fallback ? (
            <>Already uses your <span className="text-on-surface not-italic fw-500">{meta.fallback}</span> chain by default — no dedicated {meta.label} model is required. Add a backend with a chat-capable model to override.</>
          ) : (
            <>No models with {meta.label} capability. Add a backend with compatible models first.</>
          )}
        </div>
      ) : (
        <>
          {capable.length > 8 && (
            <>
              <SearchField value={query} onChange={setQuery} size="md"
                placeholder={`Search ${capable.length} models — name or provider`}
                ariaLabel="Search models" />
              {
}
              <ResultAnnouncement count={filtered.length} noun="models" active={!!query.trim()} />
            </>
          )}
          {filtered.length === 0 ? (
            <div data-type="body-s" className="rounded-md border border-dashed border-outline-variant/50 px-3 py-3 text-on-surface-low italic">
              No models match “{query}”.
            </div>
          ) : (
            <div className="-m-1 flex max-h-[300px] flex-col gap-0.5 overflow-y-auto p-1" style={{ opacity: saving ? 0.6 : 1 }}>
              {filtered.map((m) => {
            const ref = `${m.provider}:${m.id}`
            const on = activeModels.includes(ref)
            const notDownloaded = m.downloaded === false
            return (
              <div key={ref}
                className="flex items-center gap-2.5 rounded-md pr-3 transition-colors hover:bg-surface-high"
                style={on ? { background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)' } : undefined}>
                <button type="button" onClick={() => toggle(ref)} disabled={saving}
                  className="flex min-w-0 flex-1 items-center gap-2.5 rounded-md px-3 py-2 text-left">
                  <span className="grid size-4 shrink-0 place-items-center rounded border"
                    style={on ? { background: 'var(--color-primary)', borderColor: 'var(--color-primary)' } : { borderColor: 'var(--color-outline-variant)' }}>
                    {on && <Check size={10} strokeWidth={3} className="text-on-primary" />}
                  </span>
                  <span data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface font-mono">{m.name}</span>
                </button>
                <ModelChips model={m} onRepair={() => repair(m)} repairing={repairing === ref} />
                {on && notDownloaded && (
                  <span data-type="caption" className="shrink-0 inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5"
                    style={{ background: 'color-mix(in srgb, var(--color-warning) 16%, transparent)', color: 'var(--color-warning)' }}
                    title="Bound but not downloaded — download it in Providers to activate.">
                    <Download size={9} /> not downloaded
                  </span>
                )}
                <span data-type="caption" className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low">{m.provider}</span>
              </div>
            )
              })}
            </div>
          )}
        </>
      )}
    </DisclosureCard>
  )
}
