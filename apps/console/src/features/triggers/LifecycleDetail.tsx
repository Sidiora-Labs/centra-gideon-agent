import { useEffect, useState } from 'react'
import { Pencil, Trash2, Check, X, FlaskConical, AlertTriangle, ShieldOff, ShieldCheck } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { FormFooter } from '../../shared/ui/FormFooter'
import { confirmDelete } from '../../shared/ui/dialog'
import { api, type HookItem, type ActionProvider } from '../../shared/data/api'
import { Field, TextInput, FieldError } from '../../shared/ui/forms'
import { Combobox } from '../../shared/ui/Combobox'
import { Toggle } from '../../shared/ui/Toggle'
import { ActionConfig, coerceActionConfig, seedActionConfig } from './ActionConfig'
import { useTriggerVariables, lifecycleEventMeta, eventTakesToolMatcher, relPast, eventIsDormant, eventDormancyReason } from './triggerMeta'
import { accentChip } from '../../shared/theme/accent'

export function LifecycleDetail({ hook, providers, onSaved, onDeleted, editing, onEditingChange }: {
  hook: HookItem
  providers: ActionProvider[]
  onSaved: () => void
  onDeleted: () => void
  editing: boolean
  onEditingChange: (v: boolean) => void
}) {
  const setEditing = onEditingChange
  const [name, setName] = useState(hook.name)
  const [event, setEvent] = useState(hook.event)
  const [matcher, setMatcher] = useState(hook.matcher)
  const [provider, setProvider] = useState(hook.provider)
  const [config, setConfig] = useState<Record<string, unknown>>(hook.provider_config ?? {})
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [testOut, setTestOut] = useState<string | null>(null)

  const restore = () => {
    setName(hook.name); setEvent(hook.event); setMatcher(hook.matcher)
    setProvider(hook.provider); setConfig(hook.provider_config ?? {})
    setTestOut(null); setErr('')
  }
  useEffect(() => restore(), [hook.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const catalog = useTriggerVariables()
  const em = lifecycleEventMeta(catalog, event)
  const dormancyReason = eventIsDormant(catalog, hook.event) ? (eventDormancyReason(catalog, hook.event) || 'no code fires it yet') : ''
  const enforcement = hook.enforcement
  const inert = enforcement === 'not_enforcing'
  const eventOptions = (catalog?.lifecycle ?? []).map((e) => ({ value: e.event, label: e.dormant ? `${e.label} · never fires` : e.label, description: e.desc }))

  async function save() {
    if (!name.trim()) { setErr('Name is required'); return }
    const coerced = coerceActionConfig(providers, provider, config)
    if (coerced.error) { setErr(coerced.error); return }
    setSaving(true); setErr('')
    try { await api.updateHook(hook.id, { name: name.trim(), event, matcher: matcher.trim(), provider, provider_config: coerced.config }); onSaved(); setEditing(false) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }
  async function del() {
    if (!(await confirmDelete('trigger', hook.name))) return
    try { await api.deleteHook(hook.id); onDeleted() } catch { setErr('Delete failed') }
  }
  async function toggle() { setBusy(true); try { await api.toggleHook(hook.id); onSaved() } finally { setBusy(false) } }
  async function test() {
    setBusy(true); setTestOut(null)
    try {
      const r = await api.testHook(hook.id)
      const out = r.result.stdout || r.result.error || r.result.stderr || `exit ${r.result.exit_code}`
      setTestOut(`${out} · ${r.result.duration_ms}ms`)
      onSaved()
    } catch (e) { setTestOut(e instanceof Error ? e.message : 'Test failed') } finally { setBusy(false) }
  }

  function pickProvider(p: string) { setProvider(p); setConfig(seedActionConfig(providers.find((x) => x.name === p))) }

  if (editing) {
    return (
      <div className="flex flex-col gap-l">
        <Field label="Name"><TextInput value={name} onChange={setName} placeholder="Block risky writes" autoFocus /></Field>
        <Field label="Fires on" hint={em.desc}>
          <Combobox options={eventOptions} value={event} onChange={(v) => setEvent(v)} placeholder="Pick a lifecycle event…" emptyText="No events" />
        </Field>
        <Field label={eventTakesToolMatcher(event) ? 'Tool matcher' : 'Context matcher'} hint={eventTakesToolMatcher(event) ? 'Glob on tool name. Empty = all tools.' : 'Glob on the event context. Empty = always.'}>
          <TextInput value={matcher} onChange={setMatcher} placeholder={eventTakesToolMatcher(event) ? 'write_file' : '*'} />
        </Field>
        <ActionConfig providers={providers} provider={provider} config={config} onProvider={pickProvider} onConfig={setConfig} vars={em.vars} />
        {err && <FieldError>{err}</FieldError>}
        <FormFooter>
          <Button variant="ghost" size="sm" onClick={() => { restore(); setEditing(false) }}><X size={15} /> Cancel</Button>
          <Button size="sm" onClick={save} loading={saving} disabled={saving || !name.trim()}
            disabledReason={!name.trim() ? 'Enter a name first' : undefined}><Check size={15} /> Save</Button>
        </FormFooter>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-l">
      <div className="flex flex-wrap items-center gap-s">
        <Button size="sm" variant="secondary" onClick={test} loading={busy}><FlaskConical size={14} /> Test</Button>
        <Button size="sm" variant="ghost" onClick={() => setEditing(true)}><Pencil size={14} /> Edit</Button>
        <Button size="sm" variant="ghost" onClick={del}><Trash2 size={14} /> Delete</Button>
        <label className="ml-auto inline-flex items-center gap-2 text-[0.8125rem] cursor-pointer">
          <span className="text-on-surface-var">{hook.enabled ? 'Enabled' : 'Disabled'}</span>
          <Toggle on={hook.enabled} onChange={() => toggle()} disabled={busy} label="Toggle enabled" size="sm" />
        </label>
      </div>
      {err && <FieldError>{err}</FieldError>}
      {testOut && <p className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] break-words">{testOut}</p>}

      <div className="flex flex-wrap items-center gap-s">
        <span className="inline-flex items-center rounded-pill px-m h-7 text-[0.8125rem]" style={accentChip}>{em.label}</span>
        {
}
        {dormancyReason && (
          <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)', color: 'var(--color-warn)' }} title={`Nothing fires this event yet — ${dormancyReason}`}>
            <AlertTriangle size={13} /> Never fires
          </span>
        )}
        <span className="rounded-pill bg-surface-high px-m h-7 inline-flex items-center text-on-surface-var text-[0.8125rem]">{hook.provider}</span>
        {
}
        {inert && (
          <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)', color: 'var(--color-warn)' }} title="This hook still runs, but its exit code is discarded — only a hook an agent's triggers list references can reject a tool.">
            <ShieldOff size={13} /> Not enforcing
          </span>
        )}
        {enforcement === 'enforcing' && (
          <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-ok) 14%, transparent)', color: 'var(--color-ok)' }} title="Bound to an agent, so exiting 2 rejects the tool.">
            <ShieldCheck size={13} /> Enforcing
          </span>
        )}
        {hook.matcher && <span className="rounded-pill bg-surface-high px-m h-7 inline-flex items-center font-mono text-on-surface-var text-[0.75rem]">{hook.matcher}</span>}
      </div>
      {inert && (
        <p className="rounded-md px-m py-2 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)', color: 'var(--color-warn)' }}>
          This is a blocking hook that cannot block. It runs and its output is logged, but nothing
          reads its exit code, so a tool it means to deny runs anyway. Add it to an agent's{' '}
          <span className="font-mono">triggers</span> list to arm it.
        </p>
      )}

      <Section label="Action config">
        {Object.keys(hook.provider_config ?? {}).length > 0
          ? <pre className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.75rem] font-mono overflow-x-auto whitespace-pre-wrap break-words">{JSON.stringify(hook.provider_config, null, 2)}</pre>
          : <p className="text-on-surface-low text-[0.8125rem]">No configuration.</p>}
      </Section>

      <Section label="Stats">
        <span className="text-on-surface-var text-[0.8125rem]">Ran {hook.run_count}× · last {relPast(hook.last_run)}{hook.last_status ? ` · ${hook.last_status}` : ''} · timeout {hook.timeout}s</span>
        {
}
        {dormancyReason && hook.run_count === 0 && (
          <p className="text-on-surface-low text-[0.8125rem] mt-1">Zero runs is expected here: {dormancyReason}.</p>
        )}
        {
}
        {inert && hook.run_count > 0 && (
          <p className="text-on-surface-low text-[0.8125rem] mt-1">
            Those {hook.run_count} runs were advisory — the hook fired and was ignored, so none of
            them blocked anything.
          </p>
        )}
      </Section>

      <Section label="Used by">
        {hook.used_by.length > 0
          ? <div className="flex flex-wrap gap-1.5">{hook.used_by.map((a) => <span key={a} className="rounded-pill bg-surface-high px-m h-6 inline-flex items-center text-on-surface-var text-[0.75rem]">{a}</span>)}</div>
          : <p className="text-on-surface-low text-[0.8125rem]">No agents reference this trigger yet{hook.blocking ? ', so it runs but cannot block' : " — it's dormant"} until an agent's <span className="font-mono">triggers</span> list includes it.</p>}
      </Section>
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}
