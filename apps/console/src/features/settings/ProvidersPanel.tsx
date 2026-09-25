import { useState, useMemo } from 'react'
import {
  Bot, Cpu, Hash, Inbox, Bell, Wrench, ListChecks, Webhook, Sparkles,
  BookOpen, Database, FileText, Workflow, Search, RefreshCw, type LucideIcon,
} from 'lucide-react'
import { api, type SettingsProvider, type AgentRuntime, type ChannelRuntime } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { requestRunInTerminal } from '../terminal/terminalBridge'
import { useQueryParam, type RouteProps } from '../../app/shell/useQueryState'
import { Section, PanelHeader } from './settingsUI'
import { Skeleton, LoadingStatus, LoadError } from '../../shared/ui/ListScaffold'
import { ProviderCard } from './ProviderCard'
import { MultiInstanceCard } from './MultiInstanceCard'
import { RemoteModelProviders } from './ModelBackends'
import { LocalModelManager } from './LocalModelManager'
import type { ProviderModels } from '../../shared/data/api'
import { fvs } from '../../shared/theme/fontWeight'
import { ProviderConnections } from '../capabilities/platform/ProviderConnections'

const ENTITY_META: Record<string, { label: string; icon: LucideIcon; hint: string }> = {
  agent: { label: 'Agent providers', icon: Bot, hint: 'Runtimes that drive a chat — the in-process native agent and external agent CLIs (Claude Code, Codex). Enable one, then sign in to any CLI that needs it.' },
  model: { label: 'Model providers', icon: Cpu, hint: 'Contribute models to the pool you bind to use cases in Models. Native bundled models run in-process; remote providers are multi-instance connections.' },
  search: { label: 'Search providers', icon: Search, hint: 'Web-search backends you bind to use cases in Search. Configure a provider (endpoint / API key) here, then assign it per use case.' },
  channel: { label: 'Channel providers', icon: Hash, hint: 'Interaction surfaces you reach the system through — initiate sessions and talk to agents from each channel.' },
  inbox: { label: 'Inbox providers', icon: Inbox, hint: 'Each contributes its own items into the unified inbox you pull into chats.' },
  notification: { label: 'Notification providers', icon: Bell, hint: 'Channels notifications can be routed to.' },
  tool: { label: 'Tool providers', icon: Wrench, hint: 'Contribute tools the native agent can call. MCP and OpenAI-compatible servers are multi-instance.' },
  task: { label: 'Task providers', icon: ListChecks, hint: 'Contribute tasks into one combined pool the agent and you manage.' },
  action: { label: 'Action providers', icon: Webhook, hint: 'Actions a trigger can fire when it runs, grouped by what they act on.' },
  skills: { label: 'Skill providers', icon: Sparkles, hint: 'Marketplaces you install skills from; installed skills live on the filesystem for agents to reference.' },
  knowledge: { label: 'Knowledge providers', icon: BookOpen, hint: 'Contribute knowledge entities into one shared pool the system draws on.' },
  memory: { label: 'Memory providers', icon: Database, hint: 'Ordered fallbacks providing memory CRUD — the primary serves unless it is unavailable.' },
  prompt: { label: 'Prompt providers', icon: FileText, hint: 'Contribute prompts into the system.' },
  workflow: { label: 'Workflow providers', icon: Workflow, hint: 'Contribute workflows into the system.' },
  sync: { label: 'Sync transports', icon: RefreshCw, hint: 'Storage you own that more than one machine syncs through — a git repo, a synced folder, a bucket. Configure one here, then choose it under Backups → Sync.' },
}
const ENTITY_ORDER = ['agent', 'model', 'search', 'channel', 'inbox', 'notification', 'tool', 'task', 'action', 'skills', 'knowledge', 'memory', 'prompt', 'workflow', 'sync']

const ACTION_ENTITY_LABELS: Record<string, string> = {
  task: 'Task actions', agent: 'Agent actions', comms: 'Messaging actions',
  notification: 'Notification actions', shell: 'Shell actions', script: 'Script actions', webhook: 'Webhook actions',
}
const ACTION_ENTITY_ORDER = ['task', 'agent', 'comms', 'notification', 'shell', 'script', 'webhook']

export function ProvidersPanel({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const [openProvider, setOpenProvider] = useQueryParam(query, setQuery, 'open', '')
  const [connection, setConnection] = useQueryParam(query, setQuery, 'connection', '')
  const openCfg = (name: string) => (v: boolean) => setOpenProvider(v ? name : '')

  const { data: providers, status: providersStatus, error: providersError, refresh: refreshProviders } = useQuery(
    'settings:providers', () => api.settingsProviders(), { persist: true },
  )
  const { data: runtimesData, refresh: refreshRuntimes } = useQuery(
    'settings:agent-runtimes', () => api.agentRuntimes().catch(() => [] as AgentRuntime[]), { persist: true },
  )
  const { data: availableData, refresh: refreshAvailable } = useQuery(
    'settings:models-available', () => api.modelsAvailable().catch(() => [] as ProviderModels[]), { persist: true },
  )
  const availableByProvider = useMemo(() => {
    const m = new Map<string, ProviderModels>()
    for (const r of availableData ?? []) m.set(r.name, r)
    return m
  }, [availableData])
  const { data: channelsData, refresh: refreshChannels } = useQuery(
    'settings:channels', () => api.channels().catch(() => [] as ChannelRuntime[]), { persist: true },
  )
  const channelByName = useMemo(() => {
    const m = new Map<string, ChannelRuntime>()
    for (const c of channelsData ?? []) m.set(c.name, c)
    return m
  }, [channelsData])
  const matchChannel = (providerName: string, map: Map<string, ChannelRuntime>): ChannelRuntime | undefined =>
    map.get(providerName) ?? map.get(providerName.replace(/-channel$/, ''))
  const [runtimeOverride, setRuntimeOverride] = useState<AgentRuntime[] | null>(null)
  const runtimes = runtimeOverride ?? runtimesData ?? []

  const reload = () => { invalidateKeys('settings:providers'); invalidateKeys('settings:models-available'); refreshProviders(); refreshRuntimes(); refreshAvailable() }

  const recheckRuntimes = async () => {
    try { setRuntimeOverride(await api.agentRuntimes(true)) } catch {   }
  }

  const pollAfterSignIn = async (id: string) => {
    for (let i = 0; i < 12; i++) {
      await new Promise((r) => setTimeout(r, 2500))
      let rts: AgentRuntime[] = []
      try { rts = await api.agentRuntimes(true) } catch { continue }
      setRuntimeOverride(rts)
      const rt = rts.find((r) => r.provider_id === id || r.name === id)
      if (rt && rt.state !== 'needs_login') return
    }
  }

  if (providersStatus === 'error') {
    return <LoadError what="provider settings" error={providersError} onRetry={refreshProviders} />
  }

  if (!providers) return <ProvidersSkeleton />

  const byType = new Map<string, SettingsProvider[]>()
  for (const p of providers) {
    const t = p.provider?.type || 'other'
    if (!byType.has(t)) byType.set(t, [])
    byType.get(t)!.push(p)
  }
  const runtimeByExt = new Map<string, AgentRuntime>()
  for (const r of runtimes) if (r.extension) runtimeByExt.set(r.extension, r)

  const onSignIn = (rt: AgentRuntime) => {
    if (rt.login_command?.length) requestRunInTerminal(rt.login_command.join(' '))
    void pollAfterSignIn(rt.provider_id || rt.name)
  }

  const orderedTypes = [...ENTITY_ORDER.filter((t) => byType.has(t)), ...[...byType.keys()].filter((t) => !ENTITY_ORDER.includes(t))]

  return (
    <div>
      <PanelHeader title="Providers" hint="Everything pluggable in the system, organized by the entity each provider plugs into. Enable a provider and configure it inline; a provider that serves two entities appears under each." />
      <ProviderConnections selected={connection} onSelect={setConnection} />
      {orderedTypes.map((type) => {
        const meta = ENTITY_META[type] ?? { label: `${type} providers`, icon: Wrench, hint: '' }
        const exts = byType.get(type) ?? []
        return (
          <EntitySection key={type} icon={meta.icon} label={meta.label} hint={meta.hint} count={exts.length}>
            {type === 'agent' && exts.map((ext) => (
              <ProviderCard key={ext.name} ext={ext} runtime={runtimeByExt.get(ext.name)} open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={reload} onSignIn={onSignIn} onRecheck={recheckRuntimes} />
            ))}

            {type === 'model' && <ModelEntitySection exts={exts} availableByProvider={availableByProvider} openProvider={openProvider} openCfg={openCfg} onChanged={reload} />}

            {type === 'action' && <ActionGroups exts={exts} openProvider={openProvider} openCfg={openCfg} onChanged={reload} />}

            {
}
            {type !== 'agent' && type !== 'model' && type !== 'action' && exts.map((ext) => (
              ext.provider?.multiInstance
                ? <MultiInstanceCard key={ext.name} ext={ext} onChanged={reload} />
                : <ProviderCard key={ext.name} ext={ext} channel={type === 'channel' ? matchChannel(ext.name, channelByName) : undefined}
                    open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={reload} onChannelChanged={refreshChannels} />
            ))}
          </EntitySection>
        )
      })}
    </div>
  )
}

function ProvidersSkeleton() {
  return (
    <div>
      <PanelHeader title="Providers" hint="Everything pluggable in the system, organized by the entity each provider plugs into. Enable a provider and configure it inline; a provider that serves two entities appears under each." />
      {Array.from({ length: 4 }).map((_, s) => (
        <section key={s} className="mb-2xl" role="status" aria-busy="true" >
        <LoadingStatus what="providers" />
          <div className="mb-1 flex items-center gap-2">
            <Skeleton className="size-4 rounded" />
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-4 w-6 rounded-pill" />
          </div>
          <Skeleton className="mb-m h-3 w-2/3" />
          <div className="flex flex-col gap-2">
            {Array.from({ length: 2 }).map((_, c) => (
              <div key={c} className="flex items-center gap-3 rounded-lg bg-surface-container px-l py-l">
                <Skeleton className="size-8 shrink-0 rounded-lg" />
                <div className="flex-1 min-w-0 space-y-2">
                  <Skeleton className="h-3.5 w-1/4" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
                <Skeleton className="h-6 w-10 shrink-0 rounded-pill" />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

function EntitySection({ icon: Icon, label, hint, count, children }: {
  icon: LucideIcon; label: string; hint: string; count: number; children: React.ReactNode
}) {
  return (
    <Section iconTone="muted" icon={Icon} hint={hint}
      title={<>{label}<span data-type="caption" className="ml-2 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low tabular-nums">{count}</span></>}>
      <div className="flex flex-col gap-2">{children}</div>
    </Section>
  )
}

type OpenCfg = { openProvider: string; openCfg: (name: string) => (v: boolean) => void }

function ModelEntitySection({ exts, availableByProvider, openProvider, openCfg, onChanged }: {
  exts: SettingsProvider[]; availableByProvider: Map<string, ProviderModels>; onChanged: () => void
} & OpenCfg) {
  const extByName = new Map(exts.map((e) => [e.name, e]))
  const localCards = [...availableByProvider.values()].filter((a) => a.local)
  const otherNative = exts.filter((e) => !availableByProvider.get(e.name)?.local && !e.provider?.multiInstance)
  return (
    <div className="flex flex-col gap-4">
      {(localCards.length > 0 || otherNative.length > 0) && (
        <div>
          <div data-type="caption" className="mb-2 text-on-surface-low uppercase tracking-wide">Native (bundled)</div>
          <div className="flex flex-col gap-2">
            {localCards.map((av) => {
              const ext = extByName.get(av.name)
              const enabled = ext ? ext.enabled : true
              return (
                <div key={av.name}>
                  {ext
                    ? <ProviderCard ext={ext} open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={onChanged} />
                    : <div data-type="label-s" className="text-on-surface" style={fvs(600)}>{av.displayName || av.name}</div>}
                  {enabled && (
                    <div className={ext ? 'mt-2 pl-4' : 'mt-2'}>
                      <LocalModelManager provider={av.name} models={av.models ?? []} searchable={av.searchable} onChanged={onChanged} />
                    </div>
                  )}
                </div>
              )
            })}
            {otherNative.map((ext) => <ProviderCard key={ext.name} ext={ext} open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={onChanged} />)}
          </div>
        </div>
      )}
      <div>
        <div data-type="caption" className="mb-2 text-on-surface-low uppercase tracking-wide">Remote (multi-instance)</div>
        <RemoteModelProviders />
      </div>
    </div>
  )
}

function ActionGroups({ exts, openProvider, openCfg, onChanged }: { exts: SettingsProvider[]; onChanged: () => void } & OpenCfg) {
  const byEntity = new Map<string, SettingsProvider[]>()
  for (const e of exts) {
    const k = e.provider?.entity || 'other'
    if (!byEntity.has(k)) byEntity.set(k, [])
    byEntity.get(k)!.push(e)
  }
  const ordered = [...ACTION_ENTITY_ORDER.filter((e) => byEntity.has(e)), ...[...byEntity.keys()].filter((e) => !ACTION_ENTITY_ORDER.includes(e))]
  return (
    <div className="flex flex-col gap-4">
      {ordered.map((entity) => (
        <div key={entity}>
          <div data-type="caption" className="mb-2 text-on-surface-low uppercase tracking-wide">{ACTION_ENTITY_LABELS[entity] ?? 'Other actions'}</div>
          <div className="flex flex-col gap-2">
            {(byEntity.get(entity) ?? []).map((ext) => <ProviderCard key={ext.name} ext={ext} open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={onChanged} />)}
          </div>
        </div>
      ))}
    </div>
  )
}
