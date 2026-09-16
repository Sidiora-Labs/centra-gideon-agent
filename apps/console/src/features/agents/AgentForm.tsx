import { useState, type ReactNode } from 'react'
import { Check } from 'lucide-react'
import { useActiveChatModelOptions } from '../../shared/data/agents'
import { Combobox } from '../../shared/ui/Combobox'
import { Field, TextInput, TextArea, Segmented } from '../../shared/ui/forms'
import { Toggle } from '../../shared/ui/Toggle'
import { APPROVAL_MODES } from './agentMeta'
import { normalizeAgentName, useAgentCapabilities, type AgentDraft, type AgentCapabilityOption } from './agentEditorState'
export { emptyDraft, toDraft, draftToPayload, type AgentDraft } from './agentEditorState'

const capabilities = [
  { key: 'skills', label: 'Skills', hint: 'Skills surfaced to this agent.' },
  { key: 'tools', label: 'Tools', hint: 'Tools this agent may call. None selected = all available tools.' },
  { key: 'triggers', label: 'Triggers', hint: 'Lifecycle triggers that fire for this agent (the agent-scoped allow-list).' },
] as const

export function AgentForm({ draft, onChange, nameLocked, compact }: { draft: AgentDraft; onChange: (draft: AgentDraft) => void; nameLocked?: boolean; compact?: boolean }) {
  const models = useActiveChatModelOptions()
  const catalog = useAgentCapabilities()
  const set = <K extends keyof AgentDraft>(key: K, value: AgentDraft[K]) => onChange({ ...draft, [key]: value })
  return <div className={`grid ${compact ? 'gap-l' : 'gap-xl'}`}>
    <FormSection title="Identity" compact={compact}>
      <Field label="Name" hint={nameLocked ? 'Names are fixed after creation — a rename would orphan every reference to this agent.' : 'Lowercase, hyphenated — e.g. research-assistant'}>
        <TextInput required value={draft.name} onChange={value => set('name', normalizeAgentName(value))} placeholder="research-assistant" autoFocus={!nameLocked} disabled={nameLocked} disabledReason="Names are fixed after creation" />
      </Field>
      <Field label="Description"><TextInput value={draft.description} onChange={value => set('description', value)} placeholder="One line: what this agent is for" /></Field>
      <Field label="Model" hint="The model this agent runs on. Auto uses the provider default."><Combobox options={[{ value: '', label: 'Auto — provider default' }, ...models.options]} value={draft.model} onChange={value => set('model', value)} placeholder="Auto — provider default" emptyText="No models" /></Field>
    </FormSection>
    <FormSection title="Instructions and voice" compact={compact}>
      <Field label="System prompt" hint="The agent's standing instructions — WHAT it does (operating rules)."><TextArea value={draft.system_prompt} onChange={value => set('system_prompt', value)} rows={compact ? 5 : 8} placeholder="You are a focused research assistant. …" /></Field>
      <Field label="Voice" hint="WHO it is — tone, opinions, bluntness, persona. Kept separate from the rules and injected high-priority so personality survives long prompts."><TextArea value={draft.voice} onChange={value => set('voice', value)} rows={compact ? 3 : 4} placeholder="Blunt and witty. Has strong opinions and states them. No hedging or filler." /></Field>
      <Field label="Natural voice" hint="Ask for plainer PROSE — answer first, no filler openers, no summary close, the shortest accurate word. A named set of patterns to avoid, not a persona: it never changes a fact, a caveat or a refusal. Any chat can override this for itself from the composer."><Toggle on={draft.natural_voice} onChange={value => set('natural_voice', value)} label="Natural voice" /></Field>
      <Field label="Approval mode" hint="How tool calls are approved during this agent's runs."><Segmented options={APPROVAL_MODES.map(mode => ({ key: mode.key || 'default', label: mode.label }))} value={draft.approval_mode || 'default'} onChange={value => set('approval_mode', value === 'default' ? '' : value)} /></Field>
    </FormSection>
    <FormSection title="Capabilities" compact={compact}>{capabilities.map(capability => <CheckList key={capability.key} label={capability.label} hint={capability.hint} options={catalog[capability.key]} value={draft[capability.key]} onChange={value => set(capability.key, value)} />)}</FormSection>
    <FormSection title="Routing and workspace" compact={compact}>
      <Field label="Specialty" hint="One line: what this agent is the specialist for. Enables a 'route to this agent?' suggestion in default-agent chats. Leave empty to never suggest it."><TextInput value={draft.specialty} onChange={value => set('specialty', value)} placeholder="e.g. Postgres performance + query optimization" /></Field>
      <Field label="Routing hints" hint="Comma-separated example utterances that should route here (e.g. 'optimize this query, why is my db slow, add an index')."><TextArea value={draft.route_hints} onChange={value => set('route_hints', value)} rows={compact ? 2 : 3} placeholder="optimize this query, why is my db slow, add an index" /></Field>
      <div className="grid grid-cols-1 gap-l sm:grid-cols-2"><Field label="Default directory" hint="Optional working dir for this agent."><TextInput value={draft.default_dir} onChange={value => set('default_dir', value)} placeholder="/abs/path (optional)" /></Field><Field label="Memory store" hint="Optional named memory namespace."><TextInput value={draft.memory_store} onChange={value => set('memory_store', value)} placeholder="(default)" /></Field></div>
    </FormSection>
  </div>
}
function FormSection({ title, compact, children }: { title: string; compact?: boolean; children: ReactNode }) {
  return <section className={`grid rounded-lg border border-outline-variant/30 bg-surface-container/20 ${compact ? 'gap-m p-m' : 'gap-l p-l'}`}><h2 data-type="label-s" className="border-l-2 border-primary pl-s text-on-surface">{title}</h2>{children}</section>
}
function CheckList({ label, hint, options, value, onChange }: { label: string; hint?: string; options: AgentCapabilityOption[]; value: string[]; onChange: (value: string[]) => void }) {
  const [search, setSearch] = useState('')
  const needle = search.trim().toLowerCase()
  const selected = new Set(value)
  const choices = options.filter(option => !needle || `${option.label} ${option.hint ?? ''}`.toLowerCase().includes(needle))
  const toggle = (id: string) => onChange(selected.has(id) ? value.filter(item => item !== id) : [...value, id])
  return <Field label={`${label}${value.length ? ` · ${value.length}` : ''}`} hint={hint}><div className="overflow-hidden rounded-md border border-outline-variant/30 bg-surface-container/40">
    {options.length > 8 && <div className="border-b border-outline-variant/25 p-s"><TextInput value={search} onChange={setSearch} placeholder={`Search ${label.toLowerCase()}…`} ariaLabel={`Search ${label.toLowerCase()}`} size="sm" surface="base" /></div>}
    {!options.length ? <p data-type="body-s" className="p-m text-on-surface-low">No {label.toLowerCase()} available.</p> : <>
      <div role="group" aria-label={label} className="max-h-56 overflow-y-auto p-1">
        {!choices.length && <p data-type="body-s" className="p-s text-on-surface-low">No matches.</p>}
        {choices.map(o => { const on = selected.has(o.value); return <button key={o.value} type="button" aria-pressed={on} onClick={() => toggle(o.value)} className="flex min-h-9 w-full items-center gap-s rounded-md p-s text-left transition-colors hover:bg-surface-high">
          <span className={`inline-flex size-4 shrink-0 items-center justify-center rounded-sm border ${on ? 'border-primary bg-primary text-on-primary' : 'border-outline-variant'}`}>{on && <Check size={12} />}</span>
          <span className="min-w-0 flex-1"><span className="flex items-center gap-1.5"><span className="truncate text-on-surface text-[0.8125rem]" title={o.label}>{o.label}</span><RiskTag risk={o.risk} /></span>{o.hint && <span className="block truncate text-on-surface-low text-[0.75rem]" title={o.hint}>{o.hint}</span>}</span>
        </button> })}
      </div>
      {value.length > 0 && <div data-type="caption" className="flex items-center justify-between border-t border-outline-variant/25 px-m py-s text-on-surface-low"><span>{value.length} selected</span><button type="button" onClick={() => onChange([])} className="min-h-6 hover:text-on-surface">Clear</button></div>}
    </>}
  </div></Field>
}
function RiskTag({ risk }: { risk?: AgentCapabilityOption['risk'] }) {
  const tones = { caution: { color: 'var(--color-warn)', label: 'Caution' }, destructive: { color: 'var(--color-danger)', label: 'Destructive' } }
  if (!risk || risk === 'safe') return null
  const { color, label } = tones[risk]
  return <span data-type="caption" className="shrink-0 rounded-md px-1.5 py-0.5 font-semibold" title={`Risk: ${label}`} style={{ color, background: `color-mix(in srgb, ${color} 16%, transparent)` }}>{label}</span>
}
