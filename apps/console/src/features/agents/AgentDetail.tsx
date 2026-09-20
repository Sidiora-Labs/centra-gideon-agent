import { useEffect, useState, type ReactNode } from 'react'
import { Pencil, Trash2, Check, X, Star, Lock, Cpu, ShieldCheck, ChevronDown, VolumeX, RefreshCw, ExternalLink } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { TextArea, FieldError } from '../../shared/ui/forms'
import { FormFooter } from '../../shared/ui/FormFooter'
import { Combobox } from '../../shared/ui/Combobox'
import { Markdown } from '../../shared/ui/Markdown'
import { confirmDelete } from '../../shared/ui/dialog'
import { Skeleton } from '../../shared/ui/ListScaffold'
import { useQuery } from '../../shared/data/data'
import { api, type SavedAgent, type DiscoveredAgent, type McpActiveServer, type AgentHook } from '../../shared/data/api'
import { AGENT_ROUTING_MUTES_KEY, canonicalAgentKey, unmuteAgent, useActiveChatModelOptions } from '../../shared/data/agents'
import { providerMeta, isReservedAgent } from './agentMeta'
import { AgentForm, toDraft, draftToPayload } from './AgentForm'
import { accentChip, toneChipSkin } from '../../shared/theme/accent'
import { useAgentRoutingNotes, useAgentTriggerNames, useAgentWrite } from './agentEditorState'
import { documentationUrl } from '../../app/shell/config'

const badgeClass = 'inline-flex min-h-7 items-center gap-1 rounded-md px-m text-[0.8125rem]'

export function NativeAgentDetail({ agent, isDefault, onSaved, onDeleted, onSetDefault, editing: editingProp, onEditingChange }: {
  agent: SavedAgent; isDefault: boolean; onSaved: () => void; onDeleted: () => void; onSetDefault: () => void; editing: boolean; onEditingChange: (value: boolean) => void
}) {
  const reserved = isReservedAgent(agent)
  const [draft, setDraft] = useState(() => toDraft(agent))
  const operation = useAgentWrite(agent.name)
  const triggerNames = useAgentTriggerNames(agent.triggers)
  useEffect(() => { setDraft(toDraft(agent)) }, [agent.name])
  const closeEditor = () => { setDraft(toDraft(agent)); operation.setError(''); onEditingChange(false) }
  const save = () => {
    if (!draft.name.trim()) { operation.setError('Name is required'); return }
    void operation.write(() => api.updateAgent(agent.name, draftToPayload(draft)), () => { onSaved(); onEditingChange(false) })
  }
  const remove = () => {
    if (isDefault) { operation.setError('Can’t delete the default agent — set another default first.'); return }
    void operation.write(async () => {
      if (!await confirmDelete('agent', agent.name)) return false
      await api.deleteAgent(agent.name)
      return true
    }, removed => { if (removed) onDeleted() }, 'Delete failed')
  }
  if (editingProp && !reserved) return <div className="grid gap-l">
    <AgentForm draft={draft} onChange={setDraft} nameLocked compact />
    {operation.error && <FieldError>{operation.error}</FieldError>}
    <FormFooter><Button size="sm" variant="ghost" onClick={closeEditor}><X size={15} /> Cancel</Button><Button size="sm" onClick={save} loading={operation.busy}><Check size={15} /> Save</Button></FormFooter>
  </div>
  return <div className="grid gap-l">
    <div className="flex flex-wrap items-center gap-s border-b border-outline-variant/30 pb-m">
      {reserved ? <span data-type="body-s" className="inline-flex items-center gap-1.5 text-on-surface-low"><Lock size={13} /> Reserved built-in — model only</span> : <>
        <Button size="sm" variant="secondary" onClick={() => { setDraft(toDraft(agent)); onEditingChange(true) }}><Pencil size={14} /> Edit</Button>
        {!isDefault && <Button size="sm" variant="ghost" onClick={onSetDefault}><Star size={14} /> Set default</Button>}
        <Button size="sm" variant="ghost" onClick={remove} disabled={operation.busy}><Trash2 size={14} /> Delete</Button>
      </>}
      {isDefault && <span data-type="caption" className="ml-auto inline-flex items-center gap-1 text-primary"><Star size={12} fill="currentColor" /> Default</span>}
    </div>
    {operation.error && <FieldError>{operation.error}</FieldError>}
    {reserved && <><p data-type="body-s" className="leading-relaxed text-on-surface-low">This is a built-in system agent (the background-chore worker, the goal-loop worker, or the goal-planner). Its definition is fixed, but you can swap which model it runs on.</p><ReservedModelEditor agent={agent} onSaved={onSaved} /></>}
    <div className="flex flex-wrap items-center gap-s"><span className={badgeClass} style={accentChip}>{reserved && <ShieldCheck size={12} />}{reserved ? 'Built-in' : providerMeta(agent.provider).label}</span>{!reserved && agent.model && <span className={`${badgeClass} bg-surface-high font-mono text-on-surface-var`}>{agent.model}</span>}{agent.approval_mode && <span className={`${badgeClass} bg-surface-high text-on-surface-var`}>{agent.approval_mode}</span>}</div>
    {agent.description && <p className="text-[0.9375rem] leading-relaxed text-on-surface">{agent.description}</p>}
    {agent.system_prompt && <Section label="System prompt"><div tabIndex={0} role="group" aria-label="System prompt" className="max-h-72 overflow-y-auto rounded-md border border-outline-variant/25 bg-surface-container/40 p-m text-[0.8125rem] leading-relaxed text-on-surface-var"><Markdown>{agent.system_prompt}</Markdown></div></Section>}
    <Caps label="Skills" items={agent.skills} /><Caps label="Tools" items={agent.tools} /><Caps label="Triggers" items={agent.triggers} resolve={triggerNames} />
    {!reserved && <AgentAdvanced key={agent.name} agentName={agent.name} />}
  </div>
}
function AgentAdvanced({ agentName }: { agentName: string }) {
  const [open, setOpen] = useState(false)
  return <div className="border-t border-outline-variant/30 pt-m"><button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)} className="flex min-h-8 items-center gap-s text-[0.8125rem] text-on-surface-var hover:text-on-surface"><ChevronDown size={15} className={`transition-transform ${open ? 'rotate-180' : ''}`} /> Advanced</button>
    {open && <div className="mt-m grid gap-l"><RoutingNotesEditor agentName={agentName} /><RoutingStatusView agentName={agentName} /><AgentMcpView agentName={agentName} /><AgentHooksView /></div>}
  </div>
}
function RoutingNotesEditor({ agentName }: { agentName: string }) {
  const notes = useAgentRoutingNotes(agentName)
  return <Section label="Routing notes"><p data-type="caption" className="mb-s text-on-surface-low">A short "when to use this agent" note the auto-router reads to pick between agents.</p>
    {notes.loadError ? <div className="grid justify-items-start gap-s"><p role="alert" data-type="caption" className="text-danger">Couldn’t load this note, so it isn’t safe to edit — saving now could overwrite what’s on disk. {notes.loadError}</p><Button size="sm" onClick={notes.retry}><RefreshCw size={14} /> Try again</Button></div> : notes.content === null ? <Skeleton className="h-16 w-full rounded-md" /> : <div className="grid gap-s">
      <TextArea value={notes.draft} onChange={notes.setDraft} rows={3} size="sm" ariaLabel="Routing notes" placeholder="e.g. Use for deep code reviews and multi-file refactors; prefers a thorough, direct style." />
      <div className="flex flex-wrap items-center gap-s"><Button size="sm" onClick={notes.save} loading={notes.busy} loadingLabel="Saving…" disabled={!notes.dirty || notes.busy} disabledReason={!notes.dirty && !notes.busy ? 'No changes to save' : undefined}><Check size={14} /> Save notes</Button>{notes.saved && <span data-type="caption" className="text-ok">Saved ✓</span>}{notes.error && <span role="alert" data-type="caption" className="text-danger">{notes.error}</span>}</div>
    </div>}
  </Section>
}
function RoutingStatusView({ agentName }: { agentName: string }) {
  const { data: status } = useQuery(AGENT_ROUTING_MUTES_KEY, api.routingStatus)
  const operation = useAgentWrite(agentName)
  const muted = status?.muted.some(agent => canonicalAgentKey(agent) === canonicalAgentKey(agentName))
  return <Section label="Routing status">{status === undefined ? <Skeleton className="h-6 w-40 rounded-md" /> : muted ? <div data-type="body-s" className="flex flex-wrap items-center gap-s"><span className="inline-flex items-center gap-1.5 text-on-surface-var"><VolumeX size={14} /> Muted — the auto-router stopped suggesting this agent.</span><Button size="sm" ariaLabel={`Unmute ${agentName}`} loading={operation.busy} onClick={() => operation.write(() => unmuteAgent(agentName), () => {}, 'Unmute failed')}>Unmute</Button>{operation.error && <span role="alert" data-type="caption" className="text-danger">{operation.error}</span>}</div> : <p data-type="caption" className="text-on-surface-low">Active — eligible for auto-routing suggestions.</p>}</Section>
}
function AgentMcpView({ agentName }: { agentName: string }) {
  const { data: servers } = useQuery<McpActiveServer[]>(`agent:mcp:${agentName}`, () => api.mcpActive(agentName).catch(() => []), { persist: false })
  return <Section label={servers ? `MCP servers · ${servers.length}` : 'MCP servers'}>{servers === undefined ? <Skeleton className="h-6 w-40 rounded-md" /> : servers.length === 0 ? <p data-type="body-s" className="text-on-surface-low italic">No MCP servers scoped to this agent.</p> : <div className="flex flex-wrap gap-s">{servers.map(server => <span key={server.name} data-type="caption" className="inline-flex min-h-6 items-center gap-1.5 rounded-md bg-surface-high px-2" style={{ color: server.enabled ? 'var(--color-on-surface-var)' : 'var(--color-on-surface-low)' }}><span className="size-1.5 rounded-full" style={{ background: server.enabled ? 'var(--color-ok)' : 'var(--color-outline)' }} />{server.name}</span>)}</div>}</Section>
}
function AgentHooksView() {
  const { data: hooks } = useQuery<Record<string, AgentHook[]>>('agent:hooks', () => api.agentHooks().catch(() => ({})), { persist: false })
  const groups = hooks ? Object.entries(hooks).filter(([, entries]) => entries.length) : []
  return <Section label="Lifecycle hooks">{hooks === undefined ? <Skeleton className="h-6 w-40 rounded-md" /> : groups.length === 0 ? <p data-type="body-s" className="text-on-surface-low italic">No lifecycle hooks configured.</p> : <div className="grid gap-m">{groups.map(([event, entries]) => <div key={event}><h3 data-type="caption" className="mb-1 font-semibold text-on-surface-var">{event}</h3><div className="grid gap-1">{entries.map((hook, index) => <div key={index} data-type="caption" className="overflow-x-auto rounded-md border border-outline-variant/25 bg-surface-container/40 px-m py-s font-mono text-on-surface-low">{hook.matcher && <span className="text-primary">[{hook.matcher}] </span>}{hook.command}{hook.source && <span className="ml-s text-on-surface-low">{hook.source}</span>}</div>)}</div></div>)}</div>}</Section>
}
function ReservedModelEditor({ agent, onSaved }: { agent: SavedAgent; onSaved: () => void }) {
  const models = useActiveChatModelOptions()
  const [model, setModel] = useState(agent.model ?? '')
  const operation = useAgentWrite(agent.name)
  useEffect(() => { setModel(agent.model ?? '') }, [agent.name])
  return <Section label="Model"><div className="flex items-center gap-s"><div className="min-w-0 flex-1"><Combobox options={[{ value: '', label: 'Auto — use chat binding' }, ...models.options]} value={model} onChange={setModel} placeholder="Auto — use chat binding" emptyText="No active chat models" /></div>{model !== (agent.model ?? '') && <Button size="sm" loading={operation.busy} onClick={() => operation.write(() => api.updateAgent(agent.name, { model }), onSaved)}><Check size={14} /> Save</Button>}</div>{operation.error && <FieldError>{operation.error}</FieldError>}</Section>
}
export function DiscoveredAgentDetail({ agent, providerId }: { agent: DiscoveredAgent; providerId: string }) {
  const pm = providerMeta(providerId)
  const parityDoc = acpParityDocUrl(providerId)
  return <div className="grid gap-l">
    <span className={`${badgeClass} justify-self-start border border-outline-variant/30 bg-surface-high text-on-surface-var`}><Lock size={13} /> {pm.label} — read-only</span>
    <p data-type="body-s" className="text-on-surface-low">This agent is defined and run by the {pm.label} runtime. It can't be edited here, but you can use it from the chat agent picker.</p>
    <div className="flex flex-wrap items-center gap-s"><span className={badgeClass} style={toneChipSkin(pm.tone, 16)}><pm.icon size={13} /> {pm.label}</span>{agent.reasoning_effort && <span className={`${badgeClass} bg-surface-high text-on-surface-var`}>{agent.reasoning_effort} effort</span>}</div>
    {agent.description && <p className="text-[0.9375rem] leading-relaxed text-on-surface">{agent.description}</p>}
    {agent.provider_agent && <Section label="Runtime agent id"><span data-type="body-s" className="font-mono text-on-surface-var">{agent.provider_agent}</span></Section>}
    {!!agent.models?.length && <Section label="Models"><div className="flex flex-wrap gap-s">{agent.models.map(model => <span key={model} data-type="caption" className="inline-flex min-h-6 items-center gap-1 rounded-md bg-surface-high px-2 font-mono text-on-surface-var"><Cpu size={11} /> {model}</span>)}</div></Section>}
    {parityDoc && <a href={parityDoc} target="_blank" rel="noreferrer" className="inline-flex w-fit items-center gap-xs text-[0.8125rem] text-primary underline decoration-primary/40 underline-offset-2">Review {pm.label} ACP parity before binding <ExternalLink size={12} aria-hidden="true" /></a>}
  </div>
}
function acpParityDocUrl(providerId: string): string | undefined {
  const id = providerId.replace(/^acp:/, '').toLowerCase()
  const section = id.includes('claude') ? 'claude-code' : id.includes('codex') ? 'codex' : id.includes('kiro') ? 'kiro-cli' : id.includes('gemini') ? 'gemini-cli-unverified' : ''
  return section ? documentationUrl(`docs/agents/acp-parity.md#${section}`) : undefined
}
function Caps({ label, items, resolve }: { label: string; items?: string[]; resolve?: Map<string, string> | null }) {
  if (!items?.length) return null
  return <Section label={`${label} · ${items.length}`}><div className="flex flex-wrap gap-s">{items.map(id => {
    const name = resolve?.get(id)
    const missing = resolve != null && !name
    return <span key={id} data-type="caption" className="inline-flex min-h-6 items-center gap-1 rounded-md bg-surface-high px-2 text-on-surface-var">{name ?? id}{missing && <span className="text-warn">· trigger no longer exists</span>}</span>
  })}</div></Section>
}
function Section({ label, children }: { label: string; children: ReactNode }) {
  return <section className="grid gap-s"><h2 data-type="caption" className="border-l-2 border-primary/40 pl-s text-on-surface-low uppercase tracking-wide">{label}</h2><div>{children}</div></section>
}
