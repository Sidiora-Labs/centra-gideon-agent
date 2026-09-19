import { useMemo, useState, useEffect, useRef } from 'react'
import { fvs } from '../../shared/theme/fontWeight'
import { Info, ArrowLeft, Check, Zap, Settings2, AlertTriangle } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { IconButton } from '../../shared/ui/IconButton'
import { Button } from '../../shared/ui/Button'
import { api, isOutcomeRoute, type EventPattern } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { qget, useQueryParam, type RouteProps } from '../../app/shell/useQueryState'
import { Field, TextInput, Segmented } from '../../shared/ui/forms'
import { Combobox } from '../../shared/ui/Combobox'
import { PageTitle } from '../../shared/ui/PageTitle'
import { ScheduleForm, emptyDraft as emptySchedule, type ScheduleDraft } from '../schedule/ScheduleForm'
import { intervalToSecs } from '../schedule/scheduleMeta'
import { ActionConfig, coerceActionConfig, seedActionConfig } from './ActionConfig'
import { findTriggerPreset, prefillDraft } from './triggerPresets'
import { schemaProps } from '../tools/schema'
import { Toggle } from '../../shared/ui/Toggle'
import {
  TRIGGER_KINDS, type TriggerKind, useTriggerVariables, lifecycleEventMeta, eventTakesToolMatcher,
  eventDormancyReason, eventIsDormant, eventIsAgentScoped, EVENT_PATTERN_META, eventPatternMeta, eventSourceIcon,
  eventSourceLabel, appEventOptions, lifecycleEventOptions, actionIsSendCapable,
} from './triggerMeta'

export function TriggerCreatePage({ onBack, onCreated, query, setQuery }: {
  onBack: () => void; onCreated: () => void
} & Pick<RouteProps, 'query' | 'setQuery'>) {
  const [kindRaw, setKindRaw] = useQueryParam(query, setQuery, 'kind', 'schedule', { replace: true })
  const kind = (TRIGGER_KINDS.some((k) => k.key === kindRaw) ? kindRaw : 'schedule') as TriggerKind
  const setKind = (k: TriggerKind) => setKindRaw(k)
  const { data: providers = [], error: providersErr, refresh: reloadProviders } =
    useQuery('triggers:action-providers', () => api.actionProviders(), { persist: true })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const errRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => { if (err) errRef.current?.scrollIntoView({ block: 'nearest' }) }, [err])

  const seed = findTriggerPreset(qget(query, 'preset'))?.prefill ?? null

  const [name, setName] = useState(() => seed?.name ?? '')
  const [provider, setProvider] = useState('')
  const [config, setConfig] = useState<Record<string, unknown>>({})
  const [failureDelivery, setFailureDelivery] = useState('inbox')
  const [dedupeFailures, setDedupeFailures] = useState(true)

  const [sched, setSched] = useState<ScheduleDraft>(() => (seed ? prefillDraft(seed) : emptySchedule()))
  const [event, setEvent] = useState('UserPromptSubmit')
  const [matcher, setMatcher] = useState('')
  const [patternRaw, setPatternRaw] = useQueryParam(query, setQuery, 'pattern', 'InboxMessage', { replace: true })
  const pattern = (EVENT_PATTERN_META.some((p) => p.pattern === patternRaw) ? patternRaw : 'InboxMessage') as EventPattern
  const [eventMatcher, setEventMatcher] = useState('')
  const pm = eventPatternMeta(pattern)
  const SourceIcon = eventSourceIcon(pm.source)

  const catalog = useTriggerVariables()

  const em = lifecycleEventMeta(catalog, event)
  const dormancyReason = eventIsDormant(catalog, event) ? (eventDormancyReason(catalog, event) || 'no code fires it yet') : ''
  const eventOptions = useMemo(() => lifecycleEventOptions(catalog), [catalog])
  const patternOptions = useMemo(() => EVENT_PATTERN_META.map((p) => ({
    value: p.pattern, label: p.label, description: p.desc,
  })), [])
  const appEvents = useMemo(() => appEventOptions(catalog), [catalog])
  const actionVars = kind === 'schedule' ? (catalog?.schedule ?? []) : kind === 'lifecycle' ? em.vars : []
  const sendCapable = actionIsSendCapable(provider)

  function pickProvider(p: string) {
    setProvider(p)
    setConfig(seedActionConfig(providers.find((x) => x.name === p)))
  }

  const seededRef = useRef(false)
  useEffect(() => {
    if (!seed || seededRef.current) return
    const p = providers.find((x) => x.name === seed.provider)
    if (!p) return
    seededRef.current = true
    setProvider(seed.provider)
    setConfig({ ...seedActionConfig(p), ...seed.config })
  }, [providers, seed])

  const requiredConfigMet = useMemo(() => {
    if (!provider) return false
    const sel = providers.find((p) => p.name === provider)
    const { required } = schemaProps(sel?.settingsSchema)
    for (const key of required) {
      const v = config[key]
      if (v === undefined || v === null || (typeof v === 'string' && v.trim() === '')) return false
    }
    return true
  }, [provider, providers, config])

  const eventMatcherMet = kind !== 'event' || !pm.matcherRequired || !!eventMatcher.trim()
  const outcomeControlsMet = kind !== 'schedule' || isOutcomeRoute(failureDelivery)
  const canSave = !!name.trim() && !!provider && requiredConfigMet && eventMatcherMet && outcomeControlsMet

  async function create() {
    if (!canSave) { setErr('Fill in the trigger name, action, and any required action fields'); return }
    const coerced = coerceActionConfig(providers, provider, config)
    if (coerced.error) { setErr(coerced.error); return }
    setSaving(true); setErr('')
    try {
      if (kind === 'schedule') {
        const body: Record<string, unknown> = {
          name: name.trim(),
          timezone: sched.timezone || '', silent: sched.silent, strict_schedule: sched.strict_schedule,
          channel: sched.channel.trim(), skip_dates: sched.skip_dates,
          failure_delivery: failureDelivery.trim(),
          failure_policy: { dedupe_hash: dedupeFailures },
        }
        if (sched.kind === 'cron') body.cron = sched.cron.trim()
        else if (sched.kind === 'every') body.every = intervalToSecs(sched.intervalValue, sched.intervalUnit)
        else if (sched.kind === 'at') body.at = sched.at
        body.action = { provider, config: coerced.config }
        await api.createSchedule(body)
      } else if (kind === 'lifecycle') {
        await api.createHook({ name: name.trim(), event, matcher: matcher.trim(), provider, provider_config: coerced.config })
      } else {
        const body: Parameters<typeof api.createEvent>[0] = {
          name: name.trim(), pattern, action: { provider, config: coerced.config },
        }
        if (pm.matcher) body[pm.matcher] = eventMatcher.trim()
        await api.createEvent(body)
      }
      onCreated()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Create failed') } finally { setSaving(false) }
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back" size={40} onClick={onBack} /><PageTitle>New trigger</PageTitle></div>} />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-l pb-2xl flex flex-col gap-xl" style={{ maxWidth: 'var(--content-width)' }}>
          {
}
          {seed && (
            <p className="text-on-surface-low text-[0.8125rem]">
              Filled in from the <span style={fvs(600)}>{seed.name}</span> preset — change anything before saving.
            </p>
          )}
          <Field label="Name" hint="A short label for this trigger."><TextInput required value={name} onChange={setName} placeholder="Morning briefing" autoFocus /></Field>

          { }
          <SectionHeader icon={Zap} title="Trigger" subtitle="When this fires" />
          <Field label="Trigger type" hint={TRIGGER_KINDS.find((k) => k.key === kind)?.hint}>
            <Segmented options={TRIGGER_KINDS.map((k) => ({ key: k.key, label: k.label, tone: k.tone, icon: k.icon }))} value={kind} onChange={(v) => setKind(v as TriggerKind)} />
          </Field>
          {kind === 'schedule' ? (
            <ScheduleForm draft={sched} onChange={setSched} triggerOnly />
          ) : kind === 'lifecycle' ? (
            <>
              <Field label="Fires on" hint={em.desc}>
                <Combobox options={eventOptions} value={event} onChange={(v) => { setEvent(v); setMatcher('') }} placeholder="Pick a lifecycle event…" emptyText="No events" />
                {
}
                {dormancyReason && (
                  <div role="note" className="mt-2 flex items-start gap-2 rounded-lg px-3 py-2 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)', color: 'var(--color-warn)' }}>
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                    <span className="min-w-0 flex-1">
                      Nothing fires <span style={fvs(600)}>{em.label}</span> yet — {dormancyReason}. This trigger will save and stay idle.
                    </span>
                  </div>
                )}
                {
}
                {!dormancyReason && eventIsAgentScoped(catalog, event) && (
                  <div role="note" data-type="body-s" className="mt-2 flex items-start gap-2 rounded-lg bg-info/10 px-3 py-2 text-info">
                    <Info size={14} className="mt-0.5 shrink-0" />
                    <span className="min-w-0 flex-1">
                      <span style={fvs(600)}>{em.label}</span> is agent-scoped: it fires only for agents whose <code>triggers</code> list references this trigger. Until one does, it will not run.
                    </span>
                  </div>
                )}
              </Field>
              <Field label={eventTakesToolMatcher(event) ? 'Tool matcher' : 'Context matcher'} hint={eventTakesToolMatcher(event) ? 'Glob on tool name (e.g. write_file, mcp__*). Empty = all tools.' : 'Glob on the event context. Empty = always.'}>
                <TextInput value={matcher} onChange={setMatcher} placeholder={eventTakesToolMatcher(event) ? 'write_file' : '*'} />
              </Field>
            </>
          ) : (
            <>
              {
}
              <Field label="Fires on" hint={pm.desc}>
                <Combobox options={patternOptions} value={pattern} onChange={(v) => { setPatternRaw(v); setEventMatcher('') }} placeholder="Pick a data event…" emptyText="No patterns" />
                {
}
                <div className="mt-2 inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem]">
                  <SourceIcon size={14} className="shrink-0" />
                  <span>Source: <span style={fvs(600)}>{eventSourceLabel(pm.source)}</span></span>
                </div>
              </Field>
              {pm.matcher === 'event_glob' && appEvents.length > 0 ? (
                <Field label={pm.matcherLabel} hint="Pick a declared app event. Leave empty to fire on every app event.">
                  <Combobox options={appEvents} value={eventMatcher} onChange={setEventMatcher} placeholder="Pick an app event…" emptyText="No app events" />
                </Field>
              ) : pm.matcher === 'event_glob' ? (
                <>
                  <Field label={pm.matcherLabel} hint={pm.matcherHint}>
                    <TextInput value={eventMatcher} onChange={setEventMatcher} placeholder={pm.matcherPlaceholder} mono />
                  </Field>
                  {
}
                  <div role="note" className="flex items-start gap-2 rounded-lg px-3 py-2 text-[0.8125rem] -mt-2" style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)', color: 'var(--color-warn)' }}>
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                    <span className="min-w-0 flex-1">
                      No installed app contributes a trigger source yet, so nothing fires app events. This trigger will save and stay idle until you install one.
                    </span>
                  </div>
                </>
              ) : pm.matcher ? (
                <Field label={pm.matcherLabel} hint={pm.matcherHint}>
                  <TextInput value={eventMatcher} onChange={setEventMatcher} placeholder={pm.matcherPlaceholder} mono />
                </Field>
              ) : (
                <p className="text-on-surface-low text-[0.8125rem] -mt-2">
                  Fires on every {pm.source === 'inbox' ? 'accepted inbox message' : 'memory write'} — no matcher to narrow it.
                </p>
              )}
            </>
          )}

          { }
          <SectionHeader icon={Settings2} title="Action" subtitle="What runs when it fires" />
          <ActionConfig providers={providers} provider={provider} config={config} onProvider={pickProvider}
            onConfig={setConfig} vars={actionVars}
            loadError={providers.length === 0 ? providersErr : null} onRetryProviders={reloadProviders} />
          { }
          {sendCapable && (
            <div role="note" className="flex items-start gap-2 rounded-lg px-3 py-2 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-info) 10%, transparent)', color: 'var(--color-info)' }}>
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span className="min-w-0 flex-1">
                This action <span style={fvs(600)}>sends a message</span> when it fires. Sending apps default to <span style={fvs(600)}>draft-by-default</span> — a reply is composed but held until you enable sending in the app's settings.
              </span>
            </div>
          )}

          {kind === 'schedule' && (
            <>
              <SectionHeader icon={AlertTriangle} title="Outcome notifications" subtitle="How failures reach you" />
              <Field label="Failure outcome route" hint="Use inbox, notify, none, or channel:&lt;id&gt;.">
                <TextInput value={failureDelivery} onChange={setFailureDelivery} placeholder="inbox" mono />
              </Field>
              <div className="flex items-center justify-between gap-m rounded-lg bg-surface-container px-m py-3">
                <div>
                  <div className="text-on-surface text-[0.875rem]" style={fvs(500)}>Deduplicate repeated failures</div>
                  <div className="text-on-surface-low text-[0.75rem]">Suppress the same failure notification for one hour.</div>
                </div>
                <Toggle on={dedupeFailures} onChange={setDedupeFailures} label="Deduplicate repeated failures" />
              </div>
            </>
          )}

          {err && <p ref={errRef} role="alert" className="text-danger text-[0.8125rem]">{err}</p>}
        </div>
      </div>
      <div className="shrink-0 border-t border-outline-variant/40 bg-surface/95 px-l py-3">
        <div className="mx-auto flex justify-end gap-s" style={{ maxWidth: 'var(--content-width)' }}>
          <Button variant="ghost" onClick={onBack}>Cancel</Button>
          {
}
          <Button onClick={create} loading={saving} loadingLabel="Creating…" disabled={saving || !canSave}
            disabledReason={saving ? undefined
              : !name.trim() ? 'Name the trigger first'
                : providersErr && providers.length === 0 ? "Couldn't load the action providers — retry above"
                  : !provider ? 'Pick a provider'
                    : !requiredConfigMet ? 'Complete the required settings'
                      : !outcomeControlsMet ? 'Use a valid failure outcome route'
                        : 'Set the event to match'}><Check size={16} /> Create trigger</Button>
        </div>
      </div>
    </div>
  )
}

function SectionHeader({ icon: Icon, title, subtitle }: { icon: typeof Zap; title: string; subtitle: string }) {
  return (
    <div className="flex items-center gap-s border-b border-outline-variant/40 pb-2">
      <Icon size={16} className="text-primary" />
      <span className="text-on-surface text-[0.9375rem]" style={fvs(600)}>{title}</span>
      <span className="text-on-surface-low text-[0.8125rem]">· {subtitle}</span>
    </div>
  )
}
