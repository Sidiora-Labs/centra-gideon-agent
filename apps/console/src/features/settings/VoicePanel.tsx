import { useEffect, useRef, useState } from 'react'
import { notify } from '../../app/shell/appSdk'
import { unavailableWhen } from '../../shared/ui/unavailable'
import { CheckCircle2, AlertTriangle, ArrowRight, Plus, Trash2, RefreshCw, Check, X, Wand2 } from 'lucide-react'
import { api, type LexiconTerm, type LexiconCorrection } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup, Row, Field, Toggle, SavedToast, ToggleRow } from './settingsUI'
import { FormSkeleton, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { ChipInput } from '../../shared/ui/forms'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { TextLink } from '../../shared/ui/TextLink'
import { fvs } from '../../shared/theme/fontWeight'
import { bindChord, chordFromEvent, formatChord, DEFAULT_PUSH_TO_TALK_CHORD } from '../../shared/data/pushToTalk'
import { desktopBridge } from '../../shared/data/desktopBridge'
import { ShortcutRecorder } from '../../shared/ui/ShortcutRecorder'
import { VoiceProfilesSection } from './VoiceProfilesSection'

export function VoicePanel({ go, query }: { go?: (id: string) => void; query?: Record<string, string> }) {
  const [sttSettings, setSttSettings] = useState<Record<string, unknown> | null>(null)
  const [ttsSettings, setTtsSettings] = useState<Record<string, unknown> | null>(null)

  const { data, error: loadErr, refresh } = useQuery('settings:voice', async () => {
    const [active, stt, tts] = await Promise.all([
      api.modelsActive().catch(() => ({} as Record<string, string[]>)),
      api.useCaseSettings('stt'),
      api.useCaseSettings('tts'),
    ])
    return { active, stt, tts }
  }, { persist: true })
  const active = data?.active ?? {}

  useEffect(() => {
    if (data) { setSttSettings(data.stt); setTtsSettings(data.tts) }
  }, [data])

  if (!data && loadErr) return <LoadError what="speech settings" error={loadErr} onRetry={refresh} />
  if (!data || !sttSettings || !ttsSettings) return <FormSkeleton sections={3} what="speech settings" />

  return (
    <div>
      <PanelHeader title="Speech & Transcription" hint="How voice input, spoken replies, and transcription behave. The vocabulary below biases ALL transcription — microphone input and knowledge audio/video ingestion alike. The model for each use case is bound in Models — these are the provider-agnostic settings on top of it." />
      <UseCaseVoiceSection
        title="Speech-to-text" hint="Transcribe microphone input into the composer." useCase="stt"
        enableLabel="Enable speech-to-text" boundModel={(active['stt'] ?? [])[0] ?? ''}
        settings={sttSettings} setSettings={setSttSettings} go={go}
      />
      {
}
      <UseCaseVoiceSection
        title="Text-to-speech" hint="Speak agent replies aloud." useCase="tts"
        enableLabel="Speak replies aloud" boundModel={(active['tts'] ?? [])[0] ?? ''}
        settings={ttsSettings} setSettings={setTtsSettings} go={go}
        extras={(s, save, boundModel) => {
          const speed = typeof s.speed === 'number' ? s.speed : 1.0
          const provider = boundModel.includes(':') ? boundModel.split(':', 1)[0] : ''
          const isRemoteVoice = !!provider && !PIPER_PROVIDERS.includes(provider)
          const speechVoice = typeof s.speech_voice === 'string' && s.speech_voice ? s.speech_voice : 'alloy'
          const higherIsFaster = isRemoteVoice
          const isGeminiVoice = GEMINI_TTS_PROVIDERS.includes(provider)
          return (
            <>
              {isGeminiVoice ? (
                <p data-type="body-s" className="text-on-surface-low">
                  Gemini speaks at its model's own pace with its own preset voices — its speech
                  API exposes no speaking-rate control, and it does not use the hosted personas
                  other remote voices share. The speed and persona controls apply to Piper and
                  OpenAI-compatible voices.
                </p>
              ) : (
                <Field label="Speaking speed" hint={`${speed.toFixed(2)}× — ${higherIsFaster ? 'higher' : 'lower'} is faster.`}>
                <div className="flex items-center gap-3">
                  <span data-type="caption" className="text-on-surface-low">{higherIsFaster ? 'Slow' : 'Fast'}</span>
                  <input type="range" min={0.6} max={1.6} step={0.05} value={speed}
                    onChange={(e) => setLocalSpeed(s, setTtsSettings, Number(e.target.value))}
                    onPointerUp={(e) => save({ speed: Number((e.target as HTMLInputElement).value) })}
                    onKeyUp={(e) => { if (RANGE_KEYS.has(e.key)) save({ speed: Number((e.target as HTMLInputElement).value) }) }}
                    className="flex-1 accent-[var(--color-primary)]" />
                  <span data-type="caption" className="text-on-surface-low">{higherIsFaster ? 'Fast' : 'Slow'}</span>
                  <span data-type="caption" className="w-10 text-right font-mono text-on-surface tabular-nums">{speed.toFixed(2)}×</span>
                </div>
                </Field>
              )}
              {isRemoteVoice && !isGeminiVoice && (
                <Field label="Voice persona" hint="The hosted voice used by remote TTS models.">
                  <select value={speechVoice} onChange={(e) => save({ speech_voice: e.target.value })} data-type="body-s" className={selectCls}>
                    {SPEECH_VOICES.map((v) => <option key={v} value={v}>{v}</option>)}
                  </select>
                </Field>
              )}
            </>
          )
        }}
      />
      {
}
      <VoiceProfilesSection />
      <HandsFreeSection />
      <VocabularySection scrollTo={query?.section === 'vocabulary'} />
    </div>
  )
}

function HandsFreeSection() {
  const { data, error, refresh } = useQuery('settings:voice-loop', async () => {
    const cfg = await api.gideonConfig()
    return (cfg.voice ?? {}) as Record<string, unknown>
  }, { persist: true })
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null)
  useEffect(() => { if (data) setCfg(data) }, [data])

  if (!data && error) return <LoadError what="hands-free voice settings" error={error} onRetry={refresh} />
  if (!cfg) return <FormSkeleton sections={1} what="hands-free voice settings" />

  const patch = (key: string, value: unknown, onSaved: () => void) => {
    const prev = cfg
    setCfg({ ...cfg, [key]: value })
    api.patchConfig(`voice.${key}`, value).then(onSaved).catch(() => setCfg(prev))
  }
  const phrases = (key: string) => {
    const v = cfg[key]
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
  }

  return (
    <Section title="Hands-free voice" hint="Keep listening and send only when you say a confirmation phrase. The mic button stays push-to-talk; these settings shape the hands-free loop beside it.">
      <RowGroup>
        <ChordRow
          value={typeof cfg.push_to_talk_chord === 'string' && cfg.push_to_talk_chord
            ? cfg.push_to_talk_chord
            : DEFAULT_PUSH_TO_TALK_CHORD}
          onChange={(v, cb) => patch('push_to_talk_chord', v, cb)} />
        <PhraseRow label="Confirmation phrases"
          hint="Dictation accumulates in the composer until one of these ends what you just said — so a half-finished thought is never sent."
          values={phrases('confirmation_phrases')} onChange={(v, cb) => patch('confirmation_phrases', v, cb)} />
        <PhraseRow label="Exit phrases" hint="Saying one of these throws the accumulated dictation away."
          values={phrases('exit_phrases')} onChange={(v, cb) => patch('exit_phrases', v, cb)} />
        <ToggleRow label="Mute while speaking" hint="Release the microphone and discard what it captured while a reply plays aloud. This is what stops the assistant hearing itself."
          cfg={cfg} field="duplex_mute_enabled" patch={patch} />
        <ToggleRow label="Echo filter" hint="Also drop any transcription that repeats three consecutive words the assistant just spoke — the backstop for speaker bleed."
          cfg={cfg} field="echo_filter_enabled" patch={patch} />
        <ToggleRow label="Clean text before speaking" hint="Strip code blocks, shorten URLs to their domain and paths to their filename, and drop CLI flags. The transcript always keeps the full text."
          cfg={cfg} field="clean_for_speech_enabled" patch={patch} />
        <ToggleRow label="Voice-origin disclaimer" hint="Tell the model a message was dictated so it self-corrects misheard words instead of confidently misreading them."
          cfg={cfg} field="voice_disclaimer_enabled" patch={patch} />
      </RowGroup>
    </Section>
  )
}

function ChordRow({ value, onChange }: { value: string; onChange: (next: string, onSaved: () => void) => void }) {
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }
  const shell = !!desktopBridge()

  const record = async (chord: string) => {
    if (chord === value) { setError(''); return }
    if (shell) {
      const r = await bindChord(chord)
      if (!r.ok) { setError(r.reason); return }
    }
    setError('')
    onChange(chord, flash)
  }

  return (
    <Field label="Push-to-talk shortcut"
      hint={shell
        ? 'Press it to start capturing your microphone, press it again to stop and transcribe into the composer at your cursor. It works while other apps have focus, so a capture indicator stays in the menu bar the whole time.'
        : 'Used by the desktop app for global push-to-talk. A browser tab has no global shortcuts, so this is saved for when you run the desktop app.'}>
      <div className="flex flex-wrap items-center gap-2">
        <ShortcutRecorder label="Push-to-talk shortcut" value={value}
          format={formatChord} parse={chordFromEvent} onRecord={record} />
        {value !== DEFAULT_PUSH_TO_TALK_CHORD && (
          <TextLink size="xs" onClick={async () => {
            if (shell) {
              const r = await bindChord(DEFAULT_PUSH_TO_TALK_CHORD)
              if (!r.ok) { setError(r.reason); return }
            }
            setError('')
            onChange(DEFAULT_PUSH_TO_TALK_CHORD, flash)
          }}>Reset to default</TextLink>
        )}
        <SavedToast show={saved} />
      </div>
      {error && (
        <p role="alert" data-type="caption" className="mt-1.5" style={{ color: 'var(--color-error)' }}>{error}</p>
      )}
    </Field>
  )
}

function PhraseRow({ label, hint, values, onChange }: {
  label: string
  hint: string
  values: string[]
  onChange: (next: string[], onSaved: () => void) => void
}) {
  const [saved, setSaved] = useState(false)
  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }
  return (
    <Field label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <ChipInput values={values} onChange={(v) => onChange(v, flash)} max={20} placeholder="Add a phrase…" ariaLabel={label} />
        <SavedToast show={saved} />
      </div>
    </Field>
  )
}

const PIPER_PROVIDERS = ['piper', 'piper-tts']
const GEMINI_TTS_PROVIDERS = ['google', 'google-models']
const RANGE_KEYS = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'])
const SPEECH_VOICES = ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer']

function setLocalSpeed(s: Record<string, unknown>, setter: (v: Record<string, unknown>) => void, speed: number) {
  setter({ ...s, speed })
}

function UseCaseVoiceSection({
  title, hint, useCase, enableLabel, boundModel, settings, setSettings, go, extras,
}: {
  title: string; hint: string; useCase: string; enableLabel: string
  boundModel: string
  settings: Record<string, unknown>; setSettings: (v: Record<string, unknown>) => void
  go?: (id: string) => void
  extras?: (settings: Record<string, unknown>, save: (patch: Record<string, unknown>) => void, boundModel: string) => React.ReactNode
}) {
  const [saved, setSaved] = useState(false)
  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }

  const enabled = Boolean(settings.enabled)
  const bound = !!boundModel
  const modelLabel = boundModel.includes(':') ? boundModel.split(':').slice(1).join(':') : boundModel

  const saveSettings = async (patch: Record<string, unknown>) => {
    const prev = settings
    const next = { ...settings, ...patch }
    setSettings(next)
    try {
      await api.saveUseCaseSettings(useCase, next)
      flash()
    } catch (e) {
      setSettings(prev)
      notify(`Couldn't save this speech setting: ${String((e as Error)?.message || e)}`, 'error')
    }
  }

  return (
    <Section title={title} hint={hint}>
      <RowGroup>
        <Row label={enableLabel} hint={bound ? undefined : 'No model bound for this use case — bind one in Models to use this.'}>
          <div className="flex items-center gap-2">
            <AvailChip available={bound} okLabel="model bound" missLabel="no model" />
            <Toggle on={enabled} onChange={(v) => saveSettings({ enabled: v })} label={enableLabel} disabled={!bound}
              disabledReason="No model is bound for this use case — bind one in Models first" />
          </div>
        </Row>

        { }
        <Row label="Model" hint={`Bound to the ${useCase.toUpperCase()} use case — change it in Models.`}>
          {bound
            ? <span data-type="caption" className="rounded-md bg-surface-high px-2 py-1 font-mono text-on-surface">{modelLabel}</span>
            : <span data-type="body-s" className="text-on-surface-low italic">none</span>}
        </Row>

        {enabled && bound && extras?.(settings, saveSettings, boundModel)}
      </RowGroup>

      <ManageLink kind={useCase.toUpperCase()} go={go} />
      <SavedToast show={saved} />
    </Section>
  )
}

const selectCls = 'h-9 w-full max-w-sm rounded-md bg-surface-high px-3 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary cursor-pointer'

function AvailChip({ available, okLabel, missLabel }: { available: boolean; okLabel: string; missLabel: string }) {
  return (
    <span data-type="caption" className="inline-flex items-center gap-1" style={{ color: available ? 'var(--color-success)' : 'var(--color-on-surface-low)' }}>
      {available ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />} {available ? okLabel : missLabel}
    </span>
  )
}

function ManageLink({ kind, go }: { kind: string; go?: (id: string) => void }) {
  if (!go) return null
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-3">
      <TextLink onClick={() => go('models')} icon={ArrowRight} iconPosition="trailing" size="xs" ink="emphasis">
        Bind the {kind} model in Models
      </TextLink>
      {
}
      <TextLink onClick={() => go('providers')} icon={ArrowRight} iconPosition="trailing" size="xs" ink="emphasis">
        Add or download models in Providers
      </TextLink>
    </div>
  )
}


const SOURCE_BADGE: Record<string, { label: string; cls: string }> = {
  graph: { label: 'graph', cls: 'bg-surface-high text-on-surface-low' },
  manual: { label: 'manual', cls: 'bg-primary-container text-on-primary-container' },
  learned: { label: 'learned', cls: 'bg-ok/15' },
}

function VocabularySection({ scrollTo }: { scrollTo: boolean }) {
  const { data, refresh } = useQuery('settings:lexicon', async () => {
    const [terms, corrections] = await Promise.all([
      api.lexiconTerms().catch(() => ({ terms: [] as LexiconTerm[], total: 0 })),
      api.lexiconCorrections().catch(() => ({ corrections: [] as LexiconCorrection[] })),
    ])
    return { terms: terms.terms, total: terms.total, corrections: corrections.corrections }
  }, { persist: true })

  const [adding, setAdding] = useState('')
  const [busy, setBusy] = useState(false)
  const reload = () => { invalidateKeys('settings:lexicon'); refresh() }

  const anchor = useRef<HTMLDivElement | null>(null)
  const hasData = !!data
  useEffect(() => {
    if (scrollTo && hasData) anchor.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [scrollTo, hasData])

  const addTerm = async () => {
    const v = adding.trim()
    if (!v || busy) return
    setBusy(true)
    try { await api.lexiconAddTerm(v); setAdding(''); reload() } finally { setBusy(false) }
  }
  const rebuild = async () => {
    if (busy) return
    setBusy(true)
    try { await api.lexiconRebuild(); reload() } finally { setBusy(false) }
  }

  return (
    <div ref={anchor} id="vocabulary" style={{ scrollMarginTop: '1rem' }}>
      <Section title="Vocabulary & corrections" hint="Your personal lexicon — the terms that bias every transcription (mic input and knowledge audio/video ingestion) toward how you actually spell things, and the learned fixes that auto-correct mis-heard words. Auto-built from your knowledge graph; add your own or prune wrong ones.">
        {!data ? <ListSkeleton rows={5} /> : (
          <>
            <div className="mb-3 flex items-center gap-2">
              <input
                value={adding} onChange={(e) => setAdding(e.target.value)}
                aria-label="Add a vocabulary term"
                onKeyDown={(e) => { if (e.key === 'Enter') addTerm() }}
                placeholder="Add a term (e.g. Kubernetes, K8s)…"
                data-type="body-s" className="flex-1 rounded-md border border-outline-variant/50 bg-surface-container px-3 py-2 outline-none focus:border-primary" />
              <button type="button" onClick={addTerm} data-type="body-s"
                {...unavailableWhen(!adding.trim(), 'Enter a term first', { busy })}
                className="inline-flex h-9 items-center gap-1.5 rounded-md bg-primary px-3 text-on-primary disabled:opacity-40 aria-disabled:opacity-40 aria-disabled:cursor-not-allowed">
                <Plus size={15} /> Add
              </button>
              <button type="button" onClick={rebuild} disabled={busy} title="Resync from the knowledge graph"
                data-type="body-s" className="inline-flex h-9 items-center gap-1.5 rounded-md border border-outline-variant/50 px-3 text-on-surface-low hover:text-on-surface disabled:opacity-40">
                <RefreshCw size={15} className={busy ? 'animate-spin' : ''} /> Rebuild
              </button>
            </div>
            <p data-type="caption" className="mb-2 text-on-surface-low">{data.total} in your lexicon.</p>
            {data.terms.length === 0 ? (
              <div data-type="body-s" className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-6 text-center text-on-surface-low">
                No terms yet. <span className="text-on-surface">Rebuild</span> to seed from your knowledge graph, or add one above.
              </div>
            ) : (
              <div className="max-h-[45vh] overflow-y-auto rounded-lg border border-outline-variant/30 px-3">
                <div className="flex flex-col divide-y divide-outline-variant/30">
                  {data.terms.map((t) => <TermRow key={t.id} term={t} onChanged={reload} />)}
                </div>
              </div>
            )}

            {
}
            <h3 data-type="label-s" className="mt-6 mb-1 text-on-surface" style={fvs(600)}>Learned corrections</h3>
            <p data-type="caption" className="mb-2 text-on-surface-low">Fixes captured from your transcript edits. Toggle “always” to auto-apply next time.</p>
            {data.corrections.length === 0 ? (
              <div data-type="body-s" className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-6 text-center text-on-surface-low">
                No learned corrections yet. When you fix a mis-heard term in a transcript, it shows up here.
              </div>
            ) : (
              <div className="max-h-[30vh] overflow-y-auto rounded-lg border border-outline-variant/30 px-3">
                <div className="flex flex-col divide-y divide-outline-variant/30">
                  {data.corrections.map((c) => <CorrectionRow key={c.id} corr={c} onChanged={reload} />)}
                </div>
              </div>
            )}
          </>
        )}
      </Section>
    </div>
  )
}

function TermRow({ term, onChanged }: { term: LexiconTerm; onChanged: () => void }) {
  const badge = SOURCE_BADGE[term.source] ?? SOURCE_BADGE.graph
  const [busy, setBusy] = useState(false)
  const act = async (fn: () => Promise<unknown>) => { setBusy(true); try { await fn(); onChanged() } finally { setBusy(false) } }
  return (
    <div className={`flex items-center gap-2 py-2 ${term.enabled ? '' : 'opacity-50'}`}>
      <span data-type="body-s" className="flex-1 truncate">
        {term.canonical}
        {term.aliases.length > 0 && <span data-type="caption" className="ml-1.5 text-on-surface-low">({term.aliases.join(', ')})</span>}
      </span>
      <span data-type="caption" className={`rounded px-1.5 py-0.5 ${badge.cls}`}>{badge.label}</span>
      <button type="button" disabled={busy} title={term.enabled ? 'Disable (prune)' : 'Enable'}
        onClick={() => act(() => api.lexiconSetTermEnabled(term.id, !term.enabled))}
        className="inline-flex h-7 w-7 items-center justify-center rounded text-on-surface-low hover:text-on-surface disabled:opacity-40">
        {term.enabled ? <X size={14} /> : <Check size={14} />}
      </button>
      {
}
      <SquareIconButton icon={Trash2} tone="danger" label="Delete" loading={busy}
        onClick={() => act(() => api.lexiconDeleteTerm(term.id))} />
    </div>
  )
}

function CorrectionRow({ corr, onChanged }: { corr: LexiconCorrection; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const toggle = async () => { setBusy(true); try { await api.lexiconSetCorrectionAuto(corr.id, !corr.auto_apply); onChanged() } finally { setBusy(false) } }
  return (
    <div data-type="body-s" className="flex items-center gap-2 py-2">
      <span className="flex-1 truncate">
        <span className="text-on-surface-low line-through">{corr.heard}</span>
        <span className="mx-1.5 text-on-surface-low">→</span>
        <span className="text-on-surface">{corr.meant}</span>
        <span data-type="caption" className="ml-2 text-on-surface-low">×{corr.count}</span>
      </span>
      <button type="button" onClick={toggle} disabled={busy}
        title={corr.auto_apply ? 'Auto-applied — click to make it a suggestion' : 'Always fix this automatically'}
        data-type="caption" className={`inline-flex h-7 items-center gap-1 rounded px-2 transition-colors disabled:opacity-40 ${
          corr.auto_apply ? 'bg-ok/15' : 'border border-outline-variant/50 text-on-surface-low hover:text-on-surface'}`}>
        <Wand2 size={12} /> {corr.auto_apply ? 'Always' : 'Suggest'}
      </button>
    </div>
  )
}
