import { motion } from 'framer-motion'
import { Cpu, Search, Mic, MessagesSquare, Download, Check, Loader2, ShieldCheck } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { LoadError, LoadingStatus } from '../../shared/ui/ListScaffold'
import { TextLink } from '../../shared/ui/TextLink'
import { listItemEnter, stagger, spring } from '../../shared/theme/motion'
import { essentialLane, essentialCandidates, setupErrorText, useEssentialSetup, useProviderConfiguration, useChatModelBinding, type EssentialLane, type ModelPhase } from './essentialSetupState'
import { ConsentModal, PermissionConsent, CronConsentList } from '../apps/installConsent'
import { SchemaField } from '../settings/ModelBackends'
import { SchemaFieldDisclosure } from '../tools/schema'
import { type AppCatalogEntry, type OnboardingState, type OnboardingStatePatch } from '../../shared/data/api'
import { BundledModelOffer, smallestLocalChatModel } from './BundledModelOffer'
import { useQuery } from '../../shared/data/data'
import { api } from '../../shared/data/api'

type LaneId = EssentialLane

const LANES: { id: LaneId; icon: LucideIcon; title: string; blurb: string; required: boolean }[] = [
  { id: 'model', icon: Cpu, title: 'Model provider', required: true,
    blurb: 'The model your agent thinks with. Required — nothing else works without one.' },
  { id: 'search', icon: Search, title: 'Web search', required: false,
    blurb: 'Lets the agent look things up. Add a key later in Settings.' },
  { id: 'speech', icon: Mic, title: 'Speech', required: false,
    blurb: 'Speak to the agent and hear it back (transcription / voice).' },
  { id: 'channel', icon: MessagesSquare, title: 'Messaging channel', required: false,
    blurb: 'Reach your agent from a chat app. Connect it later in Settings.' },
]

function Spinner({ what, size = 16 }: { what: string; size?: number }) {
  return (
    <div role="status" aria-busy="true" className="flex items-center py-2">
      <LoadingStatus what={what} />
      <Loader2 size={size} className="animate-spin text-on-surface-low" aria-hidden="true" />
    </div>
  )
}

export const laneOf = essentialLane

export const candidatesByLane = essentialCandidates

export const CATALOG_FAILURE_GUIDANCE = 'Retry to load your app sources, or continue setup later. Browse or add a source in the Store; model connections can be configured later in Settings.'
export function emptyEssentialGuidance(lane: EssentialLane, offeredOffline: boolean): string {
  if (lane === 'model') return `${offeredOffline ? 'Download the small offline model above or connect a local model' : 'Connect a local model'}, add an app source in the Store, or continue setup and choose a model later in Settings → Models.`
  return 'Browse the Store or add an app source. You can continue setup and configure this later in Settings or Connections.'
}

const LANE_PREVIEW = 4

export function EssentialsStep({ readiness, onDone, onSkip, onProgress }: {

  readiness: OnboardingState | null

  onDone: (summary: string) => void

  onSkip: () => void

  onProgress: (patch: OnboardingStatePatch) => void
}) {
  const { catalog, catalogError, refresh, providerTypes, lanes, installed, open, expanded, modelApp, phase, boundLabel,
    pendingRef, guarded, install, confirmInstall, toggle, expand, configured, bound, selectInstalledProvider, modelVerdict, verify } = useEssentialSetup(readiness, onProgress)

  const modelReady = phase === 'done' && modelVerdict?.kind === 'ok'
  const { data: localModels } = useQuery('onboarding:local-chat-catalog', () => api.modelsAvailable())
  const offeredOffline = Boolean(smallestLocalChatModel(localModels ?? []))

  if (catalog === undefined && catalogError) {
    return (
      <div className="flex flex-col gap-m">
        <LoadError what="app catalog" error={catalogError} onRetry={refresh} />
        <p className="text-on-surface-low text-[0.8125rem]">
          {CATALOG_FAILURE_GUIDANCE}
        </p>
        <TextLink onClick={onSkip}>Set up later</TextLink>
      </div>
    )
  }
  if (catalog === undefined) {
    return <Spinner what="apps" size={18} />
  }

  return (
    <div className="grid gap-l">
      {!!catalog.sourceErrors?.length && <div role="alert" className="rounded-lg border border-warning/40 p-m text-on-surface">
        Some app sources could not be loaded. Retry the catalog or continue with the apps shown here.
        <div><Button variant="secondary" size="sm" onClick={refresh}>Retry catalog</Button></div>
      </div>}
      {LANES.map((lane) => {
        const items = lanes[lane.id]
        const isModel = lane.id === 'model'
        const shown = expanded[lane.id] ? items : items.slice(0, LANE_PREVIEW)
        const laneDone = isModel ? modelReady : items.some((e) => installed[e.name])
        return (
          <section key={lane.id} role="group" className="grid gap-s rounded-xl border border-outline/25 p-m" aria-label={lane.title}>
            <div className="flex items-baseline gap-2">
              <lane.icon size={15} className="shrink-0 translate-y-0.5 text-primary" aria-hidden="true" />
              <span className="text-on-surface text-[0.875rem]">{lane.title}</span>
              <span className="text-on-surface-low text-[0.75rem]">{lane.required ? 'Required' : 'Optional'}</span>
              {laneDone && (
                <span className="inline-flex items-center gap-1 text-[0.75rem]" style={{ color: 'var(--color-success)' }}>
                  <Check size={14} aria-hidden="true" /> Ready
                </span>
              )}
            </div>
            <p className="text-on-surface-low text-[0.8125rem]">{lane.blurb}</p>

            {isModel && <BundledModelOffer onReady={verify} />}
            {isModel && !modelVerdict && <p role="status">Checking the resolved chat model…</p>}
            {isModel && modelVerdict && modelVerdict.kind !== 'ok' && <div role="alert" className="grid gap-s text-danger">
              <p>{modelVerdict.kind === 'unknown' ? 'Could not read model readiness: ' : 'The chat model is not ready: '}{modelVerdict.message}</p>
              <Button variant="secondary" size="sm" onClick={() => void verify()}>Retry model check</Button>
            </div>}

            {isModel && phase === 'pick' && providerTypes?.filter((type) => type.capabilities?.includes('chat')).map((type) => (
              <Button key={type.type} variant="secondary" size="sm" onClick={() => selectInstalledProvider(type.app)}>
                Configure installed {type.label}
              </Button>
            ))}

            {isModel && phase !== 'pick' ? (
              <ModelSubFlow app={modelApp} phase={phase} boundLabel={boundLabel}
                onBound={bound} onConfigured={configured} />
            ) : items.length === 0 ? (
              <p className="text-on-surface-low text-[0.8125rem]">
                No {lane.title.toLowerCase()} app is available from your app sources. {emptyEssentialGuidance(lane.id, offeredOffline)}
              </p>
            ) : (
              <motion.div className="flex flex-col gap-1.5" initial="initial" animate="animate"
                variants={{ animate: { transition: stagger(0.04) } }}>
                {shown.map((e) => (
                  <AppCard key={e.name} entry={e} open={open === e.name} installed={!!installed[e.name]}
                    installable={e.installable !== false} refused={e.refused}
                    busy={guarded.busy && pendingRef.current?.name === e.name}
                    error={pendingRef.current?.name === e.name ? guarded.error : null}
                    onToggle={() => toggle(e.name)}
                    onInstall={() => install(e)} />
                ))}
                {items.length > shown.length && (
                  <TextLink onClick={() => expand(lane.id)}>
                    Showing {shown.length} of {items.length} · Show all {lane.title.toLowerCase()} apps
                  </TextLink>
                )}
              </motion.div>
            )}
          </section>
        )
      })}

      <div className="flex items-center gap-m">
        <Button variant="primary" size="md" disabled={!modelReady}
          disabledReason="Set up a model provider first — the agent can't think without one"
          onClick={() => onDone(boundLabel || 'Ready to chat')}>
          Continue
        </Button>

        <TextLink onClick={onSkip}>Set up later</TextLink>
      </div>

      {guarded.blocked && pendingRef.current && (
        <ConsentModal label={pendingRef.current.displayName || pendingRef.current.name}
          result={guarded.blocked} busy={guarded.busy}
          permissions={pendingRef.current.permissions} crons={pendingRef.current.crons}
          appUI={pendingRef.current}
          onConfirm={confirmInstall} onClose={() => guarded.reset()} />
      )}
    </div>
  )
}

function AppCard({ entry, open, installed, installable, refused, busy, error, onToggle, onInstall }: {
  entry: AppCatalogEntry; open: boolean; installed: boolean; busy: boolean
  installable: boolean; refused?: string
  error: string | null; onToggle: () => void; onInstall: () => void
}) {
  const label = entry.displayName || entry.name
  const showDetails = open && !installed
  return <motion.article variants={listItemEnter} layout transition={spring.spatialFast} className="rounded-xl border border-outline/25 bg-surface-high p-m">
    <header className="flex items-center justify-between gap-m">
      <div className="min-w-0"><h3 className="truncate text-[0.8125rem] text-on-surface">{label}</h3><p className="truncate text-[0.75rem] text-on-surface-low">{entry.description || entry.name}</p></div>
      {installed ? <span className="inline-flex shrink-0 items-center gap-1 text-[0.75rem] text-ok"><Check size={13} aria-hidden="true" /> Installed</span>
        : <Button variant="ghost" size="sm" ariaExpanded={open} onClick={onToggle}>{open ? 'Close' : 'Review'}</Button>}
    </header>
    {showDetails && <div className="mt-m grid gap-m border-t border-outline-variant pt-m">
      <PermissionConsent permissions={entry.permissions} appUI={entry} />
      {(entry.crons ?? []).length > 0 && <CronConsentList crons={entry.crons!} />}
      <p className="flex items-start gap-s text-on-surface-low" data-type="body-s"><ShieldCheck size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
        <span>Installing fetches this app behind the security scanner — a dangerous verdict is always refused.</span></p>
      {(refused || error) && <p role="alert" className="text-[0.8125rem] text-danger">{refused || error}</p>}
      <div className="flex justify-end"><Button variant="primary" size="sm" loading={busy} disabled={!installable} disabledReason={!installable ? (refused || 'This app is unavailable to install.') : undefined} onClick={onInstall}><Download size={15} aria-hidden="true" /> {installable ? `Install ${label}` : 'Unavailable'}</Button></div>
    </div>}
  </motion.article>
}

function ModelSubFlow({ app, phase, boundLabel, onConfigured, onBound }: {
  app: string; phase: ModelPhase; boundLabel: string
  onConfigured: () => void; onBound: (label: string) => void
}) {
  if (phase === 'done') {
    return (
      <p className="inline-flex items-center gap-1.5 text-[0.8125rem]" style={{ color: 'var(--color-success)' }}>
        <Check size={15} aria-hidden="true" /> {boundLabel ? `Chat model: ${boundLabel}` : 'A chat model is configured — you\'re ready.'}
      </p>
    )
  }
  if (phase === 'bind') return <BindModel onBound={onBound} />
  return <ConfigureProvider app={app} onConfigured={onConfigured} />
}

function ConfigureProvider({ app, onConfigured }: { app: string; onConfigured: () => void }) {
  const { types, typesError, refresh, t, props, values, error, busy, submit, setValue } = useProviderConfiguration(app, onConfigured)

  if (types === undefined && typesError) {
    return <LoadError what="provider types" error={typesError} onRetry={refresh} />
  }
  if (types === undefined) {
    return <Spinner what="provider types" />
  }
  if (!t) {

    return (
      <div className="flex flex-col gap-s">
        <p className="text-on-surface-var text-[0.8125rem]">
          {app} installed, but its provider type hasn't registered yet. That usually means the
          gateway needs a restart to load it.
        </p>
        <div><Button variant="secondary" size="sm" onClick={refresh}>Check again</Button></div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-m">

      <p className="text-on-surface-var text-[0.8125rem]">
        {t.label} is installed. Fill in its settings, then test the connection for real before moving on.
      </p>
      <div className="flex flex-col gap-2">
        <SchemaFieldDisclosure fields={Object.entries(props)} values={values}
          renderField={([k, f]) => <SchemaField key={k} name={k} field={f} value={values[k] ?? ''}
            onChange={(value) => setValue(k, value)} />} />
      </div>
      {error && <div className="text-danger text-[0.8125rem]" role="alert">{error}</div>}
      <div>
        <Button variant="primary" size="sm" loading={busy} onClick={submit}>Save and test</Button>
      </div>
    </div>
  )
}

function BindModel({ onBound }: { onBound: (label: string) => void }) {
  const { models, error, refresh, binding, failed, bind } = useChatModelBinding(onBound)

  if (models === undefined && error) return <LoadError what="chat models" error={error} onRetry={refresh} />
  if (models === undefined) {
    return <Spinner what="chat models" />
  }
  if (models.length === 0) {
    return (
      <div className="flex flex-col gap-s">
        <p className="text-on-surface-low text-[0.8125rem]">No chat-capable models were discovered for this provider.</p>
        <div><Button variant="secondary" size="sm" onClick={refresh}>Check again</Button></div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-m">
      <p className="text-on-surface-var text-[0.8125rem]">Pick the model the agent should chat with:</p>
      {failed && <div className="text-danger text-[0.8125rem]" role="alert">{failed}</div>}
      <motion.div className="flex flex-col gap-1.5" initial="initial" animate="animate"
        variants={{ animate: { transition: stagger(0.04) } }}>
        {models.map((m) => (
          <motion.div key={m.name} variants={listItemEnter}>
            <Button variant="ghost" size="md" shape="squircle" className="w-full justify-start"
              loading={binding === m.name} disabled={!!binding && binding !== m.name}
              disabledReason="Another model is being bound" onClick={() => bind(m)}>
              <Cpu size={15} aria-hidden="true" className="shrink-0 text-primary" />
              <span className="min-w-0 break-all text-left">{m.model_id}</span>
              <span className="shrink-0 text-on-surface-low text-[0.75rem]">{m.provider}</span>
            </Button>
          </motion.div>
        ))}
      </motion.div>
    </div>
  )
}
