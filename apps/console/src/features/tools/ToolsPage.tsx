import { useMemo, useState } from 'react'
import { withWeight } from '../../shared/theme/fontWeight'
import { Wrench, ShieldAlert, Server, Cpu, Plug, Circle, RefreshCw, Loader2, Plus, Trash2, Download, ChevronRight } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { WorkbenchLayout } from '../../shared/ui/WorkbenchLayout'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { ListControls } from '../../shared/ui/ListControls'
import { EmptyState, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { Markdown } from '../../shared/ui/Markdown'
import { SidePanel } from '../../shared/ui/SidePanel'
import { Modal } from '../../shared/ui/Modal'
import { Button } from '../../shared/ui/Button'
import { Segmented } from '../../shared/ui/Segmented'
import { Field, TextArea, TextInput } from '../../shared/ui/forms'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { Toggle as SharedToggle } from '../../shared/ui/Toggle'
import { confirm } from '../../shared/ui/dialog'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { notify } from '../../app/shell/appSdk'
import { useQueryParam, useQueryFlag, type RouteProps } from '../../app/shell/useQueryState'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { api, type ToolItem, type McpServer, type ImportableMcpServer, type ToolLoadFailure, type McpPoolStats, type ToolGroupsData } from '../../shared/data/api'
import { isKnownTrustTier, trustTierHint, trustTierLabel } from '../../shared/data/trustTier'
import { schemaProps } from './schema'
import { ToolInspector } from './ToolInspector'
import { ToolGroupsTile } from './ToolGroupsTile'
import { PageTitle } from '../../shared/ui/PageTitle'


const LOCKED_NATIVE_PROVIDER = 'gideon-filesystem'

interface Group {
  key: string
  label: string
  kind: 'native' | 'mcp'
  tools: ToolItem[]
  server?: McpServer
  providerDisabled?: boolean
  providerLocked?: boolean
  group?: string
  tier?: string
}

function serverHealth(s: McpServer): { state: string; tone: string; detail?: string } {
  if (!s.enabled) return { state: 'disabled', tone: 'var(--color-on-surface-low)' }
  if (s.status === 'ready' || s.status === 'ok' || s.status === 'connected') return { state: 'ready', tone: 'var(--color-ok)' }
  if (s.status === 'error') return { state: 'error', tone: 'var(--color-danger)', detail: s.error }
  return { state: s.status || 'unknown', tone: 'var(--color-warn)', detail: s.error }
}

interface ToolsIndexData {
  tools: ToolItem[]
  loadFailures: ToolLoadFailure[]
  servers: McpServer[]
  importable: ImportableMcpServer[]
  poolStats: McpPoolStats
  groups: ToolGroupsData | null
}

export function ToolsPage({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const { data, error: loadErr, refresh } = useQuery<ToolsIndexData>('tools:index', async () => {
    const [idx, servers, importable, poolStats, groups] = await Promise.all([
      api.toolsIndex(),
      api.mcpServers().catch(() => [] as McpServer[]),
      api.importableMcp().catch(() => [] as ImportableMcpServer[]),
      api.mcpPoolStats().catch(() => ({ available: false } as McpPoolStats)),
      api.toolGroups().catch(() => null),
    ])
    return { tools: idx.tools, loadFailures: idx.load_failures ?? [], servers, importable, poolStats, groups }
  }, { persist: true })
  const tools = data?.tools ?? null
  const loadFailures = data?.loadFailures ?? []
  const servers = data?.servers ?? []
  const importable = data?.importable ?? []
  const poolStats = data?.poolStats ?? null
  const groupsInfo = data?.groups ?? null
  const groupsEnabled = !!groupsInfo?.enabled
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [risk, setRisk] = useQueryParam(query, setQuery, 'risk', 'all', { replace: true })
  const [openNameRaw, setOpenName] = useQueryParam(query, setQuery, 'open', '')
  const openName = openNameRaw || null

  const [probing, setProbing] = useState(false)
  const [addOpen, setAddOpen] = useQueryFlag(query, setQuery, 'add')
  const load = () => { invalidateKeys('tools:index'); refresh() }

  async function reprobe() {
    setProbing(true)
    try {
      if (await reportingWrite('re-probe the MCP servers', () => api.probeMcp())) load()
    } finally { setProbing(false) }
  }

  async function toggleServer(s: McpServer) {
    const ok = await reportingWrite(`${s.enabled ? 'disable' : 'enable'} "${s.name}"`,
      () => api.toggleMcpServer(s.name, !s.enabled))
    if (ok) setTimeout(load, 400)
  }

  const [reconnecting, setReconnecting] = useState<string | null>(null)
  async function reconnectServer(s: McpServer) {
    setReconnecting(s.name)
    try { await api.reconnectMcp(s.name) } catch {   }
    finally { setReconnecting(null); load() }
  }

  async function removeServer(s: McpServer) {
    if (!(await confirm({ title: `Remove MCP server "${s.name}"?`, body: 'Its tools will no longer be available.', danger: true, confirmLabel: 'Remove' }))) return
    try {
      await api.removeMcpServer(s.name)
    } catch (e) {
      let msg = e instanceof Error ? e.message : 'Failed to remove server'
      try { const p = JSON.parse(msg); msg = p.error || msg } catch {   }
      notify(msg, 'error')
    }
    setTimeout(load, 400)
  }

  async function toggleTool(g: Group, t: ToolItem) {
    const enabled = t.disabled === true
    const what = `${enabled ? 'enable' : 'disable'} "${t.name}"`
    const ok = g.kind === 'mcp' && g.server
      ? await reportingWrite(what, () => api.toggleMcpTool(g.server!.name, t.name, enabled))
      : await reportingWrite(what, () => api.toggleTool(t.provider, t.name, enabled))
    if (ok) setTimeout(load, 300)
  }

  async function toggleProvider(g: Group) {
    if (g.kind === 'mcp' && g.server) { await toggleServer(g.server); return }
    const ok = await reportingWrite(`${g.providerDisabled ? 'enable' : 'disable'} "${g.key}"`,
      () => api.toggleToolProvider(g.key, !!g.providerDisabled))
    if (ok) setTimeout(load, 300)
  }

  const groups = useMemo<Group[] | null>(() => {
    if (!tools) return null
    const groupOf = (list: ToolItem[]): string | undefined => {
      if (!groupsEnabled) return undefined
      const counts = new Map<string, number>()
      for (const t of list) {
        if (!t.group) continue
        counts.set(t.group, (counts.get(t.group) ?? 0) + 1)
      }
      let best: string | undefined
      let bestCount = 0
      for (const [name, count] of counts) if (count > bestCount) { best = name; bestCount = count }
      return best
    }
    const needle = q.trim().toLowerCase()
    const active = !!needle || risk !== 'all'
    const match = (t: ToolItem) =>
      (!needle || `${t.name} ${t.description}`.toLowerCase().includes(needle)) &&
      (risk === 'all' || (t.risk_level ?? 'safe') === risk)
    const byProvider = new Map<string, ToolItem[]>()
    for (const t of tools) { const p = t.provider || 'other'; (byProvider.get(p) ?? byProvider.set(p, []).get(p)!).push(t) }
    const serverNames = new Set(servers.map((s) => s.name))

    const out: Group[] = []
    for (const [p, list] of byProvider) {
      if (serverNames.has(p)) continue
      const filtered = list.filter(match)
      const provOff = list.length > 0 && list.every((t) => t.providerDisabled)
      if (filtered.length || !active) out.push({
        key: p, label: p, kind: 'native', tools: filtered,
        providerDisabled: provOff, providerLocked: p === LOCKED_NATIVE_PROVIDER,
        group: groupOf(list),
        tier: list[0]?.tier,
      })
    }
    out.sort((a, b) => a.label.localeCompare(b.label))
    for (const s of servers) {
      const list = (byProvider.get(s.name) ?? []).filter(match)
      out.push({ key: s.name, label: s.name, kind: 'mcp', tools: list, server: s, group: groupOf(byProvider.get(s.name) ?? []) })
    }
    return out.filter((g) => g.tools.length > 0 || (g.kind === 'mcp' && !active) || !active)
  }, [tools, servers, q, risk, groupsEnabled])

  const open = tools?.find((t) => t.name === openName) ?? null
  const openServer = open ? servers.find((s) => s.name === open.provider) : undefined
  const filtered = !!q.trim() || risk !== 'all'
  const shownTools = (groups ?? []).reduce((n, g) => n + g.tools.length, 0)

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<PageTitle>Tools</PageTitle>}
          right={
            <HeaderActions>
              <HeaderControl icon={Plus} label="Add tool server" priority="primary" onClick={() => setAddOpen(true)} />
              <HeaderControl icon={probing ? Loader2 : RefreshCw} label="Re-probe MCP servers" priority="low" disabled={probing} onClick={reprobe} />
            </HeaderActions>
          }
        />
      }
      controls={(tools === null || tools.length > 0)
        ? <ListControls
            search={{ value: q, onChange: setQ, placeholder: 'Search tools', label: 'Search tools' }}
            results={{ count: shownTools, noun: 'tools', active: groups !== null && filtered }}
            filter={{
              value: risk, onChange: setRisk, ariaLabel: 'Filter by risk level',
              options: [
                { key: 'all', label: 'All' },
                { key: 'safe', label: 'Safe', tone: 'var(--color-ok)' },
                { key: 'caution', label: 'Caution', tone: 'var(--color-warn)' },
                { key: 'destructive', label: 'Destructive', tone: 'var(--color-danger)' },
              ],
            }}
          />
        : undefined}
      panel={open && (
        <SidePanel key={open.name} fillHeight storeKey="tool-panel-w" icon={<Wrench size={18} className="text-primary" />} title={<span className="font-mono text-[1.0625rem]">{open.name}</span>} onClose={() => setOpenName("")}>
          <ToolInspector tool={open} serverStatus={openServer ? serverHealth(openServer) : undefined} />
        </SidePanel>
      )}
    >
      <>
        <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
          {tools === null && loadErr ? (
            <LoadError what="tools" error={loadErr} onRetry={load} />
          ) : groups === null ? <ListSkeleton rows={6} what="tools" /> : groups.length === 0 ? (
            <div className="flex flex-col gap-2xl">
              <EmptyState icon={Wrench} title={filtered ? 'No matching tools' : 'No tools'} hint={filtered ? (risk !== 'all' && !q ? `No ${risk} tools.` : 'Try a different search term.') : 'Tools are the capabilities agents can invoke — built-in actions plus anything from connected MCP servers.'} />
              {!filtered && importable.length > 0 && <ImportSuggestions servers={importable} onImported={() => setTimeout(load, 300)} />}
            </div>
          ) : (
            <div className="flex flex-col gap-2xl">
              {!filtered && loadFailures.length > 0 && <LoadFailures failures={loadFailures} />}
              {!filtered && groupsInfo && <ToolGroupsTile data={groupsInfo} onChanged={load} />}
              {!filtered && <McpPoolTile stats={poolStats} />}
              {groups?.map((g) => <GroupBlock key={g.key} g={g} onOpen={setOpenName} onToggleServer={toggleServer} onRemoveServer={removeServer} onToggleTool={toggleTool} onToggleProvider={toggleProvider} onReconnect={reconnectServer} reconnecting={reconnecting} />)}
              {!filtered && importable.length > 0 && <ImportSuggestions servers={importable} onImported={() => setTimeout(load, 300)} />}
            </div>
          )}
        </div>

        {addOpen && <AddToolServerModal onClose={() => setAddOpen(false)} onAdded={() => { setAddOpen(false); setTimeout(load, 300) }} />}
      </>
    </WorkbenchLayout>
  )
}

/** Exported for test: the gate (which pool states render at all) and the conditional Evicted cell
 *  are only observable by rendering the tile against a stubbed stats object — jsdom reports every
 *  box as 0, so nothing about them is measurable from layout. */
export function McpPoolTile({ stats }: { stats: McpPoolStats | null }) {
  if (!stats || !stats.available) return null
  if (!(stats.live_connections || stats.spawns || stats.configured_servers)) return null
  const cells: Array<{ label: string; value: number | undefined; hint: string }> = [
    { label: 'Configured', value: stats.configured_servers, hint: 'MCP servers the pool knows about, whether or not a connection is open' },
    { label: 'Live', value: stats.live_connections, hint: 'Open MCP connections right now' },
    { label: 'Shared', value: stats.shared_conns, hint: 'Poolable servers shared across sessions (one process each)' },
    { label: 'Per-session', value: stats.session_conns, hint: 'Stateful servers isolated to one session' },
    { label: 'Reused', value: stats.reused, hint: 'Calls served by an existing connection instead of a new spawn' },
    { label: 'Spawns', value: stats.spawns, hint: 'Connections started this process lifetime' },
    { label: 'Reaped', value: stats.reaps, hint: 'Idle connections swept to reclaim memory' },
    ...(stats.evicted ? [{ label: 'Evicted', value: stats.evicted, hint: 'Per-session connections dropped when their session expired (shared connections are untouched)' }] : []),
  ]
  return (
    <div>
      <div className="mb-s flex items-center gap-s">
        <Server size={14} className="text-on-surface-low" />
        <span data-type="caption" className="text-on-surface-low uppercase tracking-wide">MCP connection pool</span>
      </div>
      <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))' }}>
        {cells.map((c) => (
          <div key={c.label} title={c.hint}
            className="rounded-lg border border-outline-variant/40 bg-surface-container/50 px-3 py-2">
            <div className="text-on-surface text-[1.25rem] tabular-nums leading-tight">{c.value ?? 0}</div>
            <div data-type="caption" className="text-on-surface-low">{c.label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

export function providerBadge(g: Pick<Group, 'providerLocked' | 'tier'>): { label: string; title: string } | null {
  if (g.providerLocked) {
    return { label: 'platform', title: "Ships with Gideon and is required by platform features — it can't be disabled" }
  }
  if (!isKnownTrustTier(g.tier)) return null
  return { label: trustTierLabel(g.tier), title: trustTierHint(g.tier) }
}

export function GroupBlock({ g, onOpen, onToggleServer, onRemoveServer, onToggleTool, onToggleProvider, onReconnect, reconnecting }: { g: Group; onOpen: (name: string) => void; onToggleServer: (s: McpServer) => void; onRemoveServer: (s: McpServer) => void; onToggleTool: (g: Group, t: ToolItem) => void; onToggleProvider: (g: Group) => void; onReconnect: (s: McpServer) => void; reconnecting: string | null }) {
  const health = g.server ? serverHealth(g.server) : null
  const nativeToggleable = g.kind === 'native' && !g.providerLocked
  const badge = g.kind === 'native' ? providerBadge(g) : null
  return (
    <div className={g.providerDisabled ? 'opacity-55' : ''}>
      <div className="mb-s flex items-center gap-s">
        {g.kind === 'mcp' ? <Server size={14} className="text-on-surface-low" /> : <Cpu size={14} className="text-on-surface-low" />}
        <span data-type="caption" className="text-on-surface-low uppercase tracking-wide">{g.label}</span>
        {g.kind === 'native'
          ? badge && <span data-type="caption" title={badge.title} className="rounded-pill bg-surface-high px-2 h-5 inline-flex items-center text-on-surface-low">{badge.label}</span>
          : health && <span data-type="caption" className="inline-flex items-center gap-1" style={{ color: health.tone }} title={health.detail}><Circle size={7} fill="currentColor" stroke="none" /> {health.state}</span>}
        <span data-type="caption" className="text-on-surface-low">· {g.tools.length}</span>
        {
}
        {g.group && (
          <span data-type="caption" className="rounded-pill bg-surface-high px-2 h-5 inline-flex items-center text-on-surface-low"
            title={g.group === 'core'
              ? 'Always loaded — the primitives an agent cannot work without'
              : `Group "${g.group}" — loaded on demand; a session that doesn't need it doesn't pay for its schemas`}>
            {g.group === 'core' ? 'always loaded' : `group: ${g.group}`}
          </span>
        )}
        {g.server && (
          <div className="ml-auto flex items-center gap-1">
            {
}
            <SquareIconButton label={`Reconnect ${g.server.name}`} title="Reconnect this server"
              loading={reconnecting === g.server.name} iconSize={13} onClick={() => onReconnect(g.server!)}>
              <RefreshCw size={13} />
            </SquareIconButton>
            <button onClick={() => onToggleServer(g.server!)} title={g.server.enabled ? 'Disable server' : 'Enable server'}
              aria-label={`${g.server.enabled ? 'Disable' : 'Enable'} server ${g.server.name}`}>
              <Toggle on={!!g.server.enabled} />
            </button>
            {
}
            {g.server.name.includes(':') ? (
              <span data-type="caption" className="text-on-surface-low" title={`Provided by the '${g.server.name.split(':')[0]}' app — uninstall it from the Store to remove this server.`}>via app</span>
            ) : (
              <SquareIconButton icon={Trash2} iconSize={13} tone="danger"
                label={`Remove ${g.server.name}`} title="Remove server" onClick={() => onRemoveServer(g.server!)} />
            )}
          </div>
        )}
        {nativeToggleable && (
          <div className="ml-auto flex items-center gap-1">
            <button onClick={() => onToggleProvider(g)} title={g.providerDisabled ? 'Enable this provider' : 'Disable this whole provider'}
              aria-label={`${g.providerDisabled ? 'Enable' : 'Disable'} provider ${g.label}`}>
              <Toggle on={!g.providerDisabled} />
            </button>
          </div>
        )}
        {g.providerLocked && (
          <span data-type="caption" className="ml-auto text-on-surface-low" title="Required by platform features — can't be disabled">required</span>
        )}
      </div>
      {
}
      {g.kind === 'mcp' && g.tools.length === 0 ? (
        <div data-type="body-s" className="rounded-lg bg-surface-container px-m py-3 text-on-surface-low flex items-center gap-s">
          <Plug size={14} />
          {!g.server?.enabled ? 'Server disabled.' : health?.state === 'error' ? `Not responding — ${g.server?.error || 'no tools available'}.` : 'No tools exposed yet.'}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-s sm:grid-cols-2">
          {g.tools.map((t) => {
            const { props } = schemaProps(t.parameters)
            const off = t.disabled === true
            return (
              <div key={t.name}
                className={`group flex items-start gap-s rounded-lg bg-surface-container px-m py-m transition-colors hover:bg-surface-high ${off ? 'opacity-55' : ''}`}>
                <div className="flex min-w-0 flex-1 items-start gap-s text-left">
                  <Wrench size={16} className="text-primary shrink-0 mt-0.5" />
                  <div className="min-w-0 flex-1">
                    <button type="button" onClick={() => onOpen(t.name)} className="flex max-w-full items-center gap-1.5 text-left">
                      {
}
                      <span className="truncate font-mono text-on-surface text-[0.8125rem]" title={t.name}>{t.name}</span>
                      {
}
                      {t.requires_approval && <ShieldAlert size={12} className="text-warn shrink-0" role="img" aria-label="Asks for approval before it runs" />}
                      <RiskBadge risk={t.risk_level} />
                      {off && <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low">Disabled</span>}
                    </button>
                    <div data-type="caption" className="mt-0.5 line-clamp-2 text-on-surface-low leading-snug"><Markdown inline>{t.description}</Markdown></div>
                    {props.length > 0 && <div data-type="caption" className="mt-1 text-on-surface-low">{props.length} param{props.length === 1 ? '' : 's'}</div>}
                  </div>
                </div>
                {
}
                <button
                  onClick={() => { if (!t.locked) onToggleTool(g, t) }}
                  disabled={t.locked}
                  title={t.locked ? 'Required by platform features — can’t be disabled' : off ? 'Enable this tool' : 'Disable this tool'}
                  aria-label={`${off ? 'Enable' : 'Disable'} ${t.name}`}
                  className={`shrink-0 mt-0.5 ${t.locked ? 'cursor-not-allowed opacity-40' : ''}`}>
                  <Toggle on={!off} />
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function RiskBadge({ risk }: { risk?: 'safe' | 'caution' | 'destructive' }) {
  if (!risk || risk === 'safe') return null
  const color = risk === 'destructive' ? 'var(--color-danger)' : 'var(--color-warn)'
  const label = risk === 'destructive' ? 'Destructive' : 'Caution'
  return (
    <span data-type="caption" className="rounded-pill px-1.5 py-0.5 shrink-0" title={`Risk: ${label}`}
      style={withWeight({ background: `color-mix(in srgb, ${color} 16%, transparent)`, color }, 600)}>
      {label}
    </span>
  )
}

function LoadFailures({ failures }: { failures: ToolLoadFailure[] }) {
  return (
    <div className="rounded-lg border px-m py-3" style={{ borderColor: 'color-mix(in srgb, var(--color-danger) 35%, transparent)', background: 'color-mix(in srgb, var(--color-danger) 8%, transparent)' }}>
      <div className="mb-2 flex items-center gap-s">
        <ShieldAlert size={15} className="text-danger" />
        {
}
        <span data-type="label-s" className="text-on-surface fw-500">{failures.length} tool source{failures.length === 1 ? '' : 's'} failed to load</span>
      </div>
      <div className="flex flex-col gap-1.5">
        {failures.map((f) => (
          <div key={f.provider} data-type="caption" className="leading-snug">
            <span className="font-mono text-on-surface">{f.provider}</span>
            <span className="text-on-surface-low"> — {f.error}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function Toggle({ on }: { on: boolean }) {
  return <SharedToggle on={on} readOnly decorative size="sm" />
}

function ImportSuggestions({ servers, onImported }: { servers: ImportableMcpServer[]; onImported: () => void }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)

  const importOne = async (s: ImportableMcpServer) => {
    setBusy(s.name)
    try { await api.importMcpServer(s.name); onImported() } finally { setBusy(null) }
  }

  return (
    <div>
      <button onClick={() => setOpen((v) => !v)} aria-expanded={open} className="mb-s flex min-h-6 -my-0.5 items-center gap-s text-on-surface-low hover:text-on-surface transition-colors">
        <ChevronRight size={14} style={{ transform: open ? 'rotate(90deg)' : 'none' }} />
        <Download size={14} />
        <span data-type="caption" className="uppercase tracking-wide">Discovered in other tools ({servers.length})</span>
      </button>
      {open && (
        <>
          <p data-type="caption" className="mb-2 text-on-surface-low leading-snug">
            These MCP servers are configured in another backend but not in Gideon. Import one to copy its
            configuration here so your agents can use it.
          </p>
          <div className="flex flex-col gap-2">
            {servers.map((s) => (
              <div key={s.name} className="flex items-center gap-3 rounded-lg bg-surface-container px-m py-2.5">
                <Server size={15} className="shrink-0 text-on-surface-low" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-mono text-on-surface text-[0.8125rem]" title={s.name}>{s.name}</span>
                    <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low">{s.backend}</span>
                  </div>
                  {
}
                  <p className="mt-0.5 truncate font-mono text-on-surface-low text-[0.75rem]" title={s.url || [s.command, ...(s.args ?? [])].join(' ')}>{s.url || [s.command, ...(s.args ?? [])].join(' ')}</p>
                </div>
                <Button variant="secondary" size="sm" onClick={() => importOne(s)} loading={busy === s.name}><Download size={13} /> Import
                </Button>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}


function AddToolServerModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [kind, setKind] = useState<'mcp' | 'openai'>('mcp')
  const [name, setName] = useState('')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState('')
  const [env, setEnv] = useState('')
  const [oaName, setOaName] = useState('')
  const [endpoint, setEndpoint] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [toolFilter, setToolFilter] = useState('')
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)

  const validName = /^[a-zA-Z0-9_-]{1,64}$/.test(name)
  const apiErr = (e: unknown) => {
    let msg = e instanceof Error ? e.message : 'Failed to add server'
    try { const p = JSON.parse(msg); msg = p.error || msg } catch {   }
    return msg
  }

  const submitMcp = async () => {
    if (!validName) { setErr('Name must be letters, digits, dashes, underscores (1–64).'); return }
    if (!command.trim()) { setErr('Command is required (e.g. npx, node, uvx).'); return }
    const envObj: Record<string, string> = {}
    for (const line of env.split('\n')) {
      const i = line.indexOf('=')
      if (i > 0) envObj[line.slice(0, i).trim()] = line.slice(i + 1).trim()
    }
    setSaving(true); setErr('')
    try {
      await api.addMcpServer(name.trim(), {
        command: command.trim(),
        args: args.trim() ? args.trim().split(/\s+/) : undefined,
        env: Object.keys(envObj).length ? envObj : undefined,
      })
      onAdded()
    } catch (e) { setErr(apiErr(e)); setSaving(false) }
  }

  const submitOpenai = async () => {
    if (!endpoint.trim()) { setErr('Endpoint URL is required (e.g. https://tools.example.com).'); return }
    setSaving(true); setErr('')
    try {
      await api.createProviderInstance('openai-tools', {
        display_name: oaName.trim() || endpoint.trim(),
        config: {
          endpoint: endpoint.trim(),
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
          ...(toolFilter.trim() ? { tool_filter: toolFilter.trim() } : {}),
        },
      })
      onAdded()
    } catch (e) { setErr(apiErr(e)); setSaving(false) }
  }

  const canSubmit = kind === 'mcp' ? (!!name && !!command.trim()) : !!endpoint.trim()

  return (
    <Modal title="Add tool server" icon={<Server size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-3">
        { }
        <Segmented ariaLabel="Server type" value={kind} onChange={(k) => { setKind(k as 'mcp' | 'openai'); setErr('') }}
          options={[{ key: 'mcp', label: 'MCP server' }, { key: 'openai', label: 'OpenAI tool server' }]} />

        {kind === 'mcp' ? (<>
          <Field label="Name" hint="A unique handle (letters, digits, dashes, underscores).">
            <TextInput value={name} onChange={setName} placeholder="filesystem-mcp" size="md" surface="high" />
          </Field>
          <Field label="Command" hint="The executable that starts the server over stdio.">
            <TextInput value={command} onChange={setCommand} placeholder="npx" size="md" surface="high" mono />
          </Field>
          <Field label="Arguments" hint="Space-separated args passed to the command (optional).">
            <TextInput value={args} onChange={setArgs} placeholder="-y @modelcontextprotocol/server-filesystem /path" size="md" surface="high" mono />
          </Field>
          <Field label="Environment" hint="One KEY=value per line (optional).">
            {
}
            <TextArea value={env} onChange={setEnv} rows={2} placeholder="API_KEY=sk-…" mono size="md" />
          </Field>
        </>) : (<>
          <Field label="Name" hint="A label for this tool server (optional — defaults to the endpoint).">
            <TextInput value={oaName} onChange={setOaName} placeholder="my-tools" size="md" surface="high" />
          </Field>
          <Field label="Endpoint URL" hint="Base URL of an OpenAI-compatible tool server (GET /tools, POST /tools/{name}).">
            <TextInput value={endpoint} onChange={setEndpoint} placeholder="https://tools.example.com" size="md" surface="high" mono />
          </Field>
          <Field label="API Key" hint="Optional bearer token for authentication.">
            <TextInput value={apiKey} onChange={setApiKey} placeholder="sk-…" type="password" size="md" surface="high" mono />
          </Field>
          <Field label="Tool filter" hint="Comma-separated tool names to expose. Empty = all.">
            <TextInput value={toolFilter} onChange={setToolFilter} placeholder="search, fetch" size="md" surface="high" mono />
          </Field>
        </>)}

        <div className="flex items-center gap-2">
          {
}
          <Button size="sm" onClick={kind === 'mcp' ? submitMcp : submitOpenai} loading={saving} loadingLabel="Adding…" disabled={saving || !canSubmit}
            disabledReason={saving ? undefined
              : kind === 'mcp' ? 'Name the server and give it a command' : "Enter the server's endpoint URL"}>Add server</Button>
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          {err && <span data-type="caption" style={{ color: 'var(--color-danger)' }}>{err}</span>}
        </div>
      </div>
    </Modal>
  )
}

