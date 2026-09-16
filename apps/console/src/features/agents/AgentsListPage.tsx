import { useMemo, type ReactNode } from 'react'
import { Plus, Search, Star, Users, Lock, Cpu, Wrench, Sparkles, Zap, RefreshCw } from 'lucide-react'
import { fvs } from '../../shared/theme/fontWeight'
import { TopBar } from '../../shared/ui/TopBar'
import { WorkbenchLayout } from '../../shared/ui/WorkbenchLayout'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { ListControls } from '../../shared/ui/ListControls'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { ContextMenu } from '../../shared/ui/motion'
import { SidePanel } from '../../shared/ui/SidePanel'
import { useAgentsData, type NativeGroup, type DiscoveredGroup } from './agentsData'
import { providerMeta, isReservedAgent } from './agentMeta'
import { NativeAgentDetail, DiscoveredAgentDetail } from './AgentDetail'
import type { SavedAgent, DiscoveredAgent } from '../../shared/data/api'
import { useConfigFsWatch } from '../../shared/data/useConfigFsWatch'
import { useQueryParam, useEditFlag, type RouteProps } from '../../app/shell/useQueryState'
import { PageTitle } from '../../shared/ui/PageTitle'
import { agentMatcher, decodeAgentAddress, encodeAgentAddress, useAgentLibraryActions, type AgentAddress } from './agentLibraryState'

export function AgentsListPage({ onCreate, query, setQuery }: { onCreate: () => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const { groups, error, loaded, loading, reload } = useAgentsData()
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [editing, setEditing] = useEditFlag(query, setQuery)
  const open = decodeAgentAddress(query.open ?? '')
  const setOpen = (address: AgentAddress | null) => setQuery({ open: encodeAgentAddress(address), edit: null })
  const native = groups.find((group): group is NativeGroup => group.kind === 'native')
  const discovered = groups.filter((group): group is DiscoveredGroup => group.kind === 'discovered')
  const matches = useMemo(() => agentMatcher(q), [q])
  const n = q.trim().toLowerCase()
  const shownNative = native?.agents.filter(matches) ?? []
  const runtimeGroups = discovered.map(group => ({ group, items: group.agents.filter(matches) }))
  const shownCount = runtimeGroups.reduce((count, { group, items }) => count + (group.ready ? items.length : 0), shownNative.length)
  const { syncing, syncAgents, setDefault } = useAgentLibraryActions(native?.defaultAgent, reload)
  useConfigFsWatch(open === null, path => { if (path.includes('/agents/') || path.endsWith('config.json')) reload() })
  let panel: ReactNode = null
  if (open?.kind === 'native' && native) {
    const agent = native.agents.find(candidate => candidate.name === open.name)
    if (agent) panel = <SidePanel key={`n:${agent.name}`} fillHeight storeKey="agent-panel-w" icon={<Users size={18} className="text-primary" />} title={agent.name} onClose={() => setOpen(null)}><NativeAgentDetail agent={agent} isDefault={native.defaultAgent === agent.name} editing={editing} onEditingChange={setEditing} onSaved={reload} onDeleted={() => { setOpen(null); reload() }} onSetDefault={() => { void setDefault(agent.name) }} /></SidePanel>
  } else if (open?.kind === 'discovered') {
    const group = discovered.find(candidate => candidate.providerId === open.providerId)
    const agent = group?.agents.find(candidate => candidate.id === open.id)
    const provider = providerMeta(open.providerId)
    if (agent) panel = <SidePanel key={`d:${open.providerId}:${open.id}`} fillHeight storeKey="agent-panel-w" icon={<provider.icon size={18} style={{ color: provider.tone }} />} title={agent.name} onClose={() => setOpen(null)}><DiscoveredAgentDetail agent={agent} providerId={open.providerId} /></SidePanel>
  }
  const sections: ReactNode[] = []
  if (native) sections.push(<GroupSection key="native" title="Native" icon={Users} tone="var(--color-primary)" subtitle="Built-ins run the platform — definition fixed, model swappable. Agents you create are fully editable." count={shownNative.length}>
    {shownNative.length ? <div className="grid gap-s">{shownNative.map((agent, index) => <NativeRow key={agent.name} agent={agent} index={index} isDefault={native.defaultAgent === agent.name} onClick={() => setOpen({ kind: 'native', name: agent.name })} />)}</div> : n ? <EmptyState icon={Search} title="No matching agents" hint="Try a different term." /> : <EmptyState icon={Users} title="No native agents" hint="Create an agent to define its model, system prompt, skills, tools, triggers, and workflows." action={{ label: 'New agent', onClick: onCreate, icon: Plus }} />}
  </GroupSection>)
  for (const { group, items } of runtimeGroups) {
    const provider = providerMeta(group.providerId)
    sections.push(<GroupSection key={group.providerId} title={provider.label} icon={provider.icon} tone={provider.tone} count={items.length} ready={group.ready} subtitle={group.ready ? 'Provided by the runtime — read-only.' : `Unavailable — ${group.detail || 'runtime not ready'}`}>
      {group.ready && (items.length ? <div className="grid gap-s">{items.map((agent, index) => <DiscoveredRow key={agent.id} agent={agent} index={index} tone={provider.tone} icon={provider.icon} onClick={() => setOpen({ kind: 'discovered', providerId: group.providerId, id: agent.id })} />)}</div> : <p data-type="body-s" className="text-on-surface-low">{n ? 'No matching agents.' : 'No agents discovered.'}</p>)}
    </GroupSection>)
  }
  return <WorkbenchLayout
    topBar={<TopBar keepCornerPadding left={<PageTitle>Agents</PageTitle>} right={<HeaderActions><HeaderControl icon={RefreshCw} label={syncing ? 'Syncing…' : 'Sync agents'} priority="low" onClick={syncAgents} /><HeaderControl icon={Plus} label="New agent" variant="primary" priority="primary" onClick={onCreate} /></HeaderActions>} />}
    controls={<ListControls search={{ value: q, onChange: setQ, placeholder: 'Search agents', label: 'Search agents' }} results={{ count: shownCount, noun: 'agents', active: !!n && !(loading && groups.length === 0) }} />} panel={panel}>
    <div className="mx-auto grid gap-xl px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>{!loaded && error ? <LoadError what="agents" error={error} onRetry={reload} /> : loading && !groups.length ? <ListSkeleton rows={6} what="agents" /> : sections}</div>
  </WorkbenchLayout>
}
function GroupSection({ title, icon: Icon, tone, subtitle, count, ready = true, children }: { title: string; icon: typeof Users; tone: string; subtitle: string; count: number; ready?: boolean; children: ReactNode }) {
  return <section className="rounded-lg border border-outline-variant/30 bg-surface-container/15 p-m"><header className="mb-m grid gap-s border-b border-outline-variant/25 pb-m"><div className="flex items-center gap-s"><Icon size={16} style={{ color: tone }} /><h2 data-type="label-m" className="text-on-surface">{title}</h2><span data-type="caption" className="text-on-surface-low tabular-nums">{count}</span>{!ready && <span data-type="caption" className="inline-flex items-center gap-1 text-on-surface-low"><Lock size={11} /> unavailable</span>}</div><p data-type="caption" className="text-on-surface-low">{subtitle}</p></header>{children}</section>
}
function NativeRow({ agent, index, isDefault, onClick }: { agent: SavedAgent; index: number; isDefault: boolean; onClick: () => void }) {
  return <ContextMenu items={[{ icon: <Users size={15} />, label: 'Open', onSelect: onClick }]}><ListRow index={index} accent={isDefault ? 'var(--color-primary)' : undefined} onClick={onClick} label={agent.name}>
    <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-md border border-primary/20 bg-primary/10"><Users size={19} className="text-primary" /></span>
    <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-s"><span className="truncate text-on-surface text-[0.9375rem] font-mono" style={fvs(500)} title={agent.name}>{agent.name}</span>{isDefault && <span data-type="caption" className="inline-flex shrink-0 items-center gap-1 text-primary"><Star size={11} fill="currentColor" /> default</span>}{isReservedAgent(agent) && <span data-type="caption" className="inline-flex shrink-0 items-center gap-1 text-on-surface-low"><Lock size={10} /> built-in</span>}</div>
      <div data-type="body-s" className="mt-1 flex flex-wrap items-center gap-x-m gap-y-1 text-on-surface-low">{agent.model && <span data-type="caption" className="font-mono">{agent.model}</span>}{agent.description && <span className="truncate" title={agent.description}>{agent.model ? '· ' : ''}{agent.description}</span>}</div>
    </div>
    <div data-type="caption" className="hidden shrink-0 items-center gap-m text-on-surface-low sm:flex">
      {(agent.skills?.length ?? 0) > 0 && <span role="img" aria-label={`${agent.skills!.length} skill${agent.skills!.length === 1 ? '' : 's'}`} className="inline-flex items-center gap-1"><Sparkles size={11} /> {agent.skills!.length}</span>}
      {(agent.tools?.length ?? 0) > 0 && <span role="img" aria-label={`${agent.tools!.length} tool${agent.tools!.length === 1 ? '' : 's'}`} className="inline-flex items-center gap-1"><Wrench size={11} /> {agent.tools!.length}</span>}
      {(agent.triggers?.length ?? 0) > 0 && <span role="img" aria-label={`${agent.triggers!.length} trigger${agent.triggers!.length === 1 ? '' : 's'}`} className="inline-flex items-center gap-1"><Zap size={11} /> {agent.triggers!.length}</span>}
    </div>
  </ListRow></ContextMenu>
}
function DiscoveredRow({ agent, index, tone, icon: Icon, onClick }: { agent: DiscoveredAgent; index: number; tone: string; icon: typeof Cpu; onClick: () => void }) {
  return <ContextMenu items={[{ icon: <Icon size={15} />, label: 'Open', onSelect: onClick }]}><ListRow index={index} onClick={onClick} label={agent.name}>
    <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-md border border-outline-variant/30" style={{ background: `color-mix(in srgb, ${tone} 12%, transparent)` }}><Icon size={19} style={{ color: tone }} /></span>
    <div className="min-w-0 flex-1"><span className="block truncate text-on-surface text-[0.9375rem]" style={fvs(500)} title={agent.name}>{agent.name}</span>{agent.description && <p className="mt-0.5 truncate text-on-surface-low text-[0.8125rem]" title={agent.description}>{agent.description}</p>}</div><Lock size={13} className="shrink-0 text-on-surface-low" />
  </ListRow></ContextMenu>
}
