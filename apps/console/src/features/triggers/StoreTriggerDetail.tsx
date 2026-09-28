import { Combobox } from '../../shared/ui/Combobox'
import { ActionConfig, coerceActionConfig, seedActionConfig } from './ActionConfig'
import { useEffect, useState } from 'react'
import { Field, FieldError, TextInput } from '../../shared/ui/forms'
import { Trash2, Play, FlaskConical, AlertTriangle, Users } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { Toggle } from '../../shared/ui/Toggle'
import { confirmDelete } from '../../shared/ui/dialog'
import { api, isOutcomeRoute, type ActionProvider, type EventPattern, type Trigger as WireTrigger } from '../../shared/data/api'
import { RunHistory, RunReceipt } from '../schedule/ScheduleDetail'
import { triggerHealthMeta } from '../schedule/scheduleMeta'
import { actionLabel, EVENT_PATTERN_META } from './triggerMeta'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { BUSY_REASON } from '../../shared/ui/unavailable'

export function StoreTriggerDetail({ trigger, providers = [], editing, onEditingChange, onChanged, onDeleted }: {
  trigger: WireTrigger
  providers?: ActionProvider[]
  editing?: boolean
  onEditingChange?: (editing: boolean) => void
  onChanged: () => void
  onDeleted: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [runFlash, setRunFlash] = useState<string | null>(null)
  const [runReceipt, setRunReceipt] = useState<{ status?: string; summary?: string } | null>(null)
  const [histKey, setHistKey] = useState(0)
  const [err, setErr] = useState('')
  const [delivery, setDelivery] = useState(trigger.delivery ?? 'none')
  const [failureDelivery, setFailureDelivery] = useState(trigger.failure_delivery ?? 'inbox')
  const [dedupeFailures, setDedupeFailures] = useState(trigger.failure_policy?.dedupe_hash === true)

  useEffect(() => {
    setDelivery(trigger.delivery ?? 'none')
    setFailureDelivery(trigger.failure_delivery ?? 'inbox')
    setDedupeFailures(trigger.failure_policy?.dedupe_hash === true)
  }, [trigger])

  const isEvent = trigger.store_kind === 'event'
  const isManual = trigger.store_kind === 'manual'
  const eventPattern = isEvent ? String(trigger.spec?.pattern ?? '') : ''
  const pm = EVENT_PATTERN_META.find((item) => item.pattern === eventPattern)
  const eventMatcher = pm?.matcher ? String(trigger.spec?.[pm.matcher] ?? '') : ''
  const [localEditing, setLocalEditing] = useState(false)
  const activeEditing = editing ?? localEditing
  const changeEditing = onEditingChange ?? setLocalEditing
  const [pattern, setPattern] = useState<EventPattern>((eventPattern || 'InboxMessage') as EventPattern)
  const [matcher, setMatcher] = useState(eventMatcher)
  const [provider, setProvider] = useState(trigger.action?.provider ?? '')
  const [config, setConfig] = useState<Record<string, unknown>>(trigger.action?.config ?? {})
  const draftPattern = EVENT_PATTERN_META.find((item) => item.pattern === pattern)
  useEffect(() => {
    if (activeEditing) return
    setPattern((eventPattern || 'InboxMessage') as EventPattern)
    setMatcher(eventMatcher)
    setProvider(trigger.action?.provider ?? '')
    setConfig(trigger.action?.config ?? {})
  }, [activeEditing, eventPattern, eventMatcher, trigger.action])

  async function saveEvent() {
    const coerced = coerceActionConfig(providers, provider, config)
    if (coerced.error) { setErr(coerced.error); return }
    if (!draftPattern) { setErr('Choose a supported event pattern'); return }
    if (draftPattern.matcherRequired && !matcher.trim()) { setErr(`${draftPattern.matcherLabel} is required`); return }
    setBusy(true); setErr('')
    try {
      await api.updateEventTrigger(trigger.raw_id, {
        pattern,
        ...(draftPattern.matcher ? { [draftPattern.matcher]: matcher.trim() } : {}),
        action: { provider, config: coerced.config },
      }, trigger.document_revision)
      changeEditing(false)
      onChanged()
    } catch (error) { setErr(error instanceof Error ? error.message : 'Could not save this trigger') }
    finally { setBusy(false) }
  }

  const readOnly = trigger.read_only === true
  const broken = trigger.broken ?? []
  const paths = Array.isArray(trigger.spec?.paths) ? (trigger.spec!.paths as string[]) : []

  async function toggle() {
    setBusy(true)
    setErr('')
    try {
      await api.toggleStoreTrigger(trigger.raw_id, !trigger.enabled)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not change this automation')
    } finally {
      setBusy(false)
    }
  }

  async function toggleSilent(silent: boolean) {
    setBusy(true)
    setErr('')
    try {
      await api.updateSchedule(trigger.raw_id, { silent }, trigger.document_revision)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not change delivery for this automation')
    } finally {
      setBusy(false)
    }
  }

  const lc = triggerHealthMeta(trigger.health, trigger.state)
  const stopped = trigger.state === 'autopaused' || trigger.state === 'quarantined'
  const statusLine =
    trigger.state === 'autopaused'
      ? 'Stopped by the system after repeated failures'
      : trigger.state === 'quarantined'
        ? 'Quarantined — a payload matched an injection pattern; re-author it to resume'
        : trigger.state === 'parked'
          ? 'Parked — a resource it needs is busy; it resumes on its own'
          : isManual
            ? 'Runs only when you run it — it never fires on its own'
            : trigger.enabled
            ? isEvent ? 'Listening — it fires when a matching event arrives' : 'Firing on its own'
            : 'Paused — it will not fire until re-enabled'

  async function run(dry: boolean) {
    setBusy(true)
    setErr('')
    setRunFlash(null)
    setRunReceipt(null)
    try {
      const r = await api.runStoreTrigger(trigger.raw_id, dry)
      if (!dry && r.ok === false) {
        setErr(r.refused || (typeof r.result === 'string' && r.result) || 'This automation did not run.')
        return
      }
      const plan = (r.result as { plan?: { enforced?: string[]; bypassed?: string[] } } | undefined)?.plan
      setRunFlash(dry
        ? plan ? `Dry run: no action executed. Enforced: ${(plan.enforced ?? []).join(', ') || 'none'}. Bypassed: ${(plan.bypassed ?? []).join(', ') || 'none'}.` : 'Dry run: no action executed.'
        : null)
      if (!dry) setRunReceipt({ status: r.status, summary: r.summary })
      if (!dry) setHistKey((k) => k + 1)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Run failed')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!(await confirmDelete('automation', trigger.name))) return
    setBusy(true)
    try {
      if (!(await reportingWrite(`delete ${trigger.name}`, () => api.deleteStoreTrigger(trigger.raw_id)))) return
      onDeleted()
    } finally {
      setBusy(false)
    }
  }

  const outcomeSettingsChanged = delivery !== (trigger.delivery ?? 'none')
    || failureDelivery !== (trigger.failure_delivery ?? 'inbox')
    || dedupeFailures !== (trigger.failure_policy?.dedupe_hash === true)
  const outcomeSettingsValid = isOutcomeRoute(delivery) && isOutcomeRoute(failureDelivery)

  async function saveOutcomeSettings() {
    if (!outcomeSettingsValid) {
      setErr('Use inbox, notify, none, or channel:<id> for each outcome route')
      return
    }
    setBusy(true)
    setErr('')
    try {
      await api.updateStoreTrigger(trigger.raw_id, {
        delivery: delivery.trim(),
        failure_delivery: failureDelivery.trim(),
        failure_policy: { dedupe_hash: dedupeFailures },
      }, trigger.document_revision)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save outcome notifications')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-l px-m py-m">
      {broken.length > 0 && (
        <div
          className="flex items-start gap-2 rounded-lg px-3 py-2 text-[0.8125rem]"
          style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}
        >
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <div>
            <div style={{ fontWeight: 500 }}>This automation has a problem and will not fire.</div>
            <div className="mt-0.5">{broken[0]}</div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between">
        <div>
          <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Status</div>
          <div className="flex items-center gap-1.5">
            <lc.icon size={13} style={{ color: lc.tone }} />
            <span className="text-on-surface text-[0.8125rem]">{statusLine}</span>
          </div>
          {
}
          {stopped && trigger.last_error && (
            <div className="mt-0.5 font-mono text-on-surface-low text-[0.75rem] break-all">
              {trigger.last_error}
            </div>
          )}
        </div>
        {
}
        {!isManual && (readOnly
          ? <span className="shrink-0 text-on-surface-var text-[0.8125rem]">{trigger.enabled ? 'Enabled' : 'Disabled'}</span>
          : <Toggle on={trigger.enabled} onChange={toggle} disabled={busy} label="Enabled" />)}
      </div>

      <div className="flex items-center justify-between">
        <div>
          <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Delivery</div>
          <div className="text-on-surface text-[0.8125rem]">
            {trigger.silent ? 'Silent — automatic delivery is suppressed' : 'Automatic delivery is available'}
          </div>
        </div>
        {readOnly
          ? <span className="shrink-0 text-on-surface-var text-[0.8125rem]">{trigger.silent ? 'Silent' : 'Delivery on'}</span>
          : <Toggle on={trigger.silent === true} onChange={toggleSilent} disabled={busy} label="Silent" />}
      </div>

      {readOnly && (
        <div className="rounded-lg bg-surface-container px-3 py-2 text-on-surface-var text-[0.8125rem]">
          <span className="inline-flex items-center gap-1.5 text-on-surface"><Users size={13} /> {trigger.author || 'Someone else'}</span>
          {' '}wrote this automation. It is shown for reference: this harness never runs it, and it
          cannot be edited or deleted here.
        </div>
      )}

      <Section label="When it runs">
        <div data-type="body-m" className="text-on-surface">{isEvent ? pm?.label || (eventPattern ? `A data event (${eventPattern})` : 'Data event') : storeKindLabel(trigger.store_kind)}</div>
        {pm && <p className="mt-0.5 text-on-surface-low text-[0.8125rem]">{pm.desc}</p>}
        {pm?.matcher && <p className="mt-1 text-[0.8125rem]"><span className="text-on-surface-low">{pm.matcherLabel}: </span>
          <span className="font-mono text-on-surface break-all">{eventMatcher || (pm.matcherRequired ? 'none — this trigger cannot fire' : 'every event')}</span></p>}
        {paths.length > 0 && (
          <ul className="mt-1 flex flex-col gap-0.5">
            {paths.map((p) => (
              <li key={p} className="font-mono text-on-surface-low text-[0.8125rem] break-all">{p}</li>
            ))}
          </ul>
        )}
      </Section>

      <Section label="What it runs">
        <div data-type="body-m" className="text-on-surface">{actionLabel(trigger.action?.provider)}</div>
      </Section>

      {isEvent && !readOnly && <Section label="Edit data event">
        {!activeEditing ? <Button size="sm" variant="ghost" onClick={() => changeEditing(true)}>Edit event</Button> : <div className="flex flex-col gap-m rounded-lg bg-surface-container p-m">
          <Field label="Fires on"><Combobox options={EVENT_PATTERN_META.map((item) => ({ value: item.pattern, label: item.label }))}
            value={pattern} onChange={(value) => { setPattern(value as EventPattern); setMatcher('') }} placeholder="Choose an event" /></Field>
          {draftPattern?.matcher && <Field label={draftPattern.matcherLabel} hint={draftPattern.matcherHint}>
            <TextInput value={matcher} onChange={setMatcher} placeholder={draftPattern.matcherPlaceholder} /></Field>}
          <ActionConfig providers={providers} provider={provider} config={config}
            onProvider={(name) => { setProvider(name); setConfig(seedActionConfig(providers.find((item) => item.name === name))) }} onConfig={setConfig} vars={[]} />
          <div className="flex gap-s"><Button size="sm" onClick={saveEvent} loading={busy}>Save changes</Button>
            <Button size="sm" variant="ghost" onClick={() => changeEditing(false)} disabled={busy}>Cancel</Button></div>
        </div>}
      </Section>}

      {!readOnly && (
        <Section label="Outcome notifications">
          <div className="flex flex-col gap-m">
            <Field label="Successful run route" hint="Use inbox, notify, none, or channel:<id>.">
              <TextInput value={delivery} onChange={setDelivery} placeholder="none" mono />
            </Field>
            <Field label="Failed run route" hint="A separate route lets failures escape a muted success route.">
              <TextInput value={failureDelivery} onChange={setFailureDelivery} placeholder="inbox" mono />
            </Field>
            <div className="flex items-center justify-between gap-m">
              <span className="text-on-surface text-[0.8125rem]">Deduplicate repeated failures</span>
              <Toggle on={dedupeFailures} onChange={setDedupeFailures} label="Deduplicate repeated failures" disabled={busy} />
            </div>
            <div className="flex justify-end">
              <Button variant="secondary" size="sm" onClick={saveOutcomeSettings} loading={busy}
                disabled={!outcomeSettingsChanged || !outcomeSettingsValid || busy}
                disabledReason={!outcomeSettingsValid ? 'Use a valid outcome route' : !outcomeSettingsChanged ? 'No changes to save' : BUSY_REASON}>
                Save notifications
              </Button>
            </div>
          </div>
        </Section>
      )}

      {
}
      <RunHistory triggerId={trigger.id} reloadKey={histKey} />

      {trigger.created_by === 'agent' && (
        <div className="text-on-surface-low text-[0.75rem]">Created for you automatically. Manage it here or ask in chat to change it.</div>
      )}

      {err && <FieldError>{err}</FieldError>}
      {runReceipt && !err && <RunReceipt {...runReceipt} />}
      {runFlash && !err && <div className="text-on-surface-low text-[0.8125rem]">{runFlash}</div>}

      {
}
      {!readOnly && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <Button variant="secondary" size="sm" onClick={() => run(false)} loading={busy}><Play size={14} /> Run now
          </Button>
          <Button variant="ghost" size="sm" onClick={() => run(true)} disabled={busy} disabledReason={BUSY_REASON}>
            <FlaskConical size={14} /> Dry run
          </Button>
          <div className="flex-1" />
          <Button variant="ghost" size="sm" onClick={remove} disabled={busy} disabledReason={BUSY_REASON} className="text-danger">
            <Trash2 size={14} /> Delete
          </Button>
        </div>
      )}
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">{label}</div>
      {children}
    </div>
  )
}

function storeKindLabel(kind?: string): string {
  const map: Record<string, string> = {
    file: 'When a watched file changes',
    web_watch: 'When a watched web page changes',
    idle: 'After a period of inactivity',
    run_completed: 'When a workflow run finishes',
    view: 'When its surface is viewed',
    webhook: 'When its webhook receives a request',
    manual: 'Only when you run it',
  }
  return map[kind ?? ''] ?? (kind || 'Automation')
}
