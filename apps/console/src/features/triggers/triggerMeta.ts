import { useEffect, useState } from 'react'
import { CalendarClock, Webhook, Bell, MessageSquare, ListPlus, Users, TerminalSquare, FileCode2, Zap, Anchor, Bot, Workflow, FolderClock, Globe, Moon, FileText, Inbox, Database, Plug, Wrench } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api, type ScheduleJob, type HookItem, type HookEnforcement, type LifecycleEventInfo, type TriggerVariables, type Trigger as WireTrigger, type EventPattern } from '../../shared/data/api'
import { deriveKind, deriveMode, kindMeta as schedKindMeta, modeMeta as schedModeMeta } from '../schedule/scheduleMeta'
import { statusMeta, triggerHealthMeta, type StatusMeta } from '../schedule/scheduleMeta'
import { epochSeconds } from '../../shared/data/epoch'

export type TriggerKind = 'schedule' | 'lifecycle' | 'event' | 'store'
export interface TriggerKindMeta { key: TriggerKind; label: string; icon: LucideIcon; tone: string; hint: string }
export const TRIGGER_KINDS: TriggerKindMeta[] = [
  { key: 'schedule', label: 'Schedule', icon: CalendarClock, tone: 'var(--color-info)', hint: 'Fires on a clock — every N, on a cron, or once at a set time.' },
  { key: 'lifecycle', label: 'Lifecycle event', icon: Anchor, tone: 'var(--color-primary)', hint: 'Fires on an agent-loop event — a tool call, a prompt, session end, …' },
  { key: 'event', label: 'Data event', icon: Inbox, tone: 'var(--color-secondary)', hint: 'Fires on an inbox message, a memory write, or an app-contributed event matching a pattern you choose.' },
]

export type EventMatcherField = 'sender_glob' | 'address_glob' | 'key_glob' | 'content_re' | 'event_glob' | null
export interface EventPatternMeta {
  pattern: EventPattern
  source: 'inbox' | 'memory' | 'app'
  label: string
  desc: string
  matcher: EventMatcherField
  matcherLabel: string
  matcherHint: string
  matcherPlaceholder: string
  matcherRequired: boolean
}
export const EVENT_PATTERN_META: EventPatternMeta[] = [
  { pattern: 'InboxMessage', source: 'inbox', label: 'Any inbox message', desc: 'Every accepted message from a watched inbox source (Slack, Telegram, email, …).', matcher: null, matcherLabel: '', matcherHint: '', matcherPlaceholder: '', matcherRequired: false },
  { pattern: 'InboxSender', source: 'inbox', label: 'Inbox message from a sender', desc: 'An inbox message whose sender matches a glob.', matcher: 'sender_glob', matcherLabel: 'Sender glob', matcherHint: 'Glob on the sender id (e.g. alice@example.com, U*, +1415*). Required.', matcherPlaceholder: 'alice@example.com', matcherRequired: true },
  { pattern: 'InboxAddress', source: 'inbox', label: 'Inbox message to an address', desc: 'An inbox message whose receiving address/channel matches a glob.', matcher: 'address_glob', matcherLabel: 'Address glob', matcherHint: 'Glob on the receiving address or channel (e.g. support@*, #alerts). Empty matches all.', matcherPlaceholder: 'support@*', matcherRequired: false },
  { pattern: 'MemoryUpdate', source: 'memory', label: 'Any memory write', desc: 'Every memory create, update, or delete.', matcher: null, matcherLabel: '', matcherHint: '', matcherPlaceholder: '', matcherRequired: false },
  { pattern: 'MemoryKeyPattern', source: 'memory', label: 'Memory write to a key', desc: 'A memory write whose key matches a glob.', matcher: 'key_glob', matcherLabel: 'Key glob', matcherHint: 'Glob on the memory key (e.g. project.acme.*). Empty matches nothing.', matcherPlaceholder: 'project.acme.*', matcherRequired: false },
  { pattern: 'ContentMatch', source: 'memory', label: 'Memory write matching content', desc: "A memory write whose value matches a regex (or substring if it isn't valid regex).", matcher: 'content_re', matcherLabel: 'Content matcher', matcherHint: 'Regex matched against the written value (substring fallback). Empty matches nothing.', matcherPlaceholder: 'invoice|payment', matcherRequired: false },
  { pattern: 'AppEvent', source: 'app', label: 'App event', desc: 'An event from an installed app that contributes a trigger source (a calendar, a device, a watched service).', matcher: 'event_glob', matcherLabel: 'Event', matcherHint: 'Pick a declared event, or glob the namespaced name (e.g. app:my-source:*). Empty matches every app event.', matcherPlaceholder: 'app:my-source:*', matcherRequired: false },
]
export function eventPatternMeta(pattern?: string): EventPatternMeta {
  return EVENT_PATTERN_META.find((p) => p.pattern === pattern) ?? EVENT_PATTERN_META[0]
}
export function eventSourceIcon(source: string): LucideIcon {
  if (source === 'inbox') return Inbox
  if (source === 'app') return Plug
  return Database
}
export function eventSourceLabel(source: string): string {
  if (source === 'inbox') return 'Inbox'
  if (source === 'app') return 'App'
  return 'Memory'
}
export function appEventOptions(catalog: TriggerVariables | null): { value: string; label: string; description: string }[] {
  return (catalog?.app_sources ?? []).flatMap((s) =>
    s.events.map((e) => ({ value: e.source_event, label: `${s.label} · ${e.event}`, description: e.source_event })),
  )
}

export function lifecycleEventOptions(
  catalog: TriggerVariables | null,
): { value: string; label: string; description: string; group?: string }[] {
  const all = catalog?.lifecycle ?? []
  const anyDormant = all.some((e) => e.dormant)
  return [...all]
    .sort((a, b) => Number(!!a.dormant) - Number(!!b.dormant))
    .map((e) => ({
      value: e.event,
      label: e.dormant ? `${e.label} · never fires` : e.label,
      description: e.desc,
      group: anyDormant ? (e.dormant ? 'Advanced — nothing fires these yet' : 'Live events') : undefined,
    }))
}

const STORE_KIND_META: Record<string, { label: string; icon: LucideIcon }> = {
  file: { label: 'On file change', icon: FolderClock },
  web_watch: { label: 'On web page change', icon: Globe },
  idle: { label: 'When idle', icon: Moon },
  run_completed: { label: 'When a run finishes', icon: Workflow },
  view: { label: 'View trigger', icon: FileText },
  webhook: { label: 'On webhook', icon: Webhook },
}
function storeKindMeta(storeKind?: string): { label: string; icon: LucideIcon } {
  return STORE_KIND_META[storeKind ?? ''] ?? { label: storeKind || 'Automation', icon: Zap }
}
export type LifecycleEventMeta = LifecycleEventInfo

let _catalogCache: TriggerVariables | null = null
let _catalogPromise: Promise<TriggerVariables> | null = null

export function useTriggerVariables(): TriggerVariables | null {
  const [cat, setCat] = useState<TriggerVariables | null>(_catalogCache)
  useEffect(() => {
    if (_catalogCache) { setCat(_catalogCache); return }
    if (!_catalogPromise) _catalogPromise = api.triggerVariables().then((c) => { _catalogCache = c; return c })
    let alive = true
    _catalogPromise.then((c) => { if (alive) setCat(c) }).catch(() => { _catalogPromise = null })
    return () => { alive = false }
  }, [])
  return cat
}

export function lifecycleEventMeta(cat: TriggerVariables | null, event?: string): LifecycleEventMeta {
  const list = cat?.lifecycle ?? []
  return list.find((e) => e.event === event) ?? list[0] ?? { event: event ?? '', label: event ?? '', desc: '', vars: [], blocking: false }
}
export function eventIsDormant(cat: TriggerVariables | null, event?: string): boolean {
  if (!event) return false
  return Boolean(cat?.lifecycle?.find((e) => e.event === event)?.dormant)
}
export function eventDormancyReason(cat: TriggerVariables | null, event?: string): string {
  if (!event) return ''
  const found = cat?.lifecycle?.find((e) => e.event === event)
  return found?.dormant ? (found.dormant_reason ?? '') : ''
}
export function eventIsAgentScoped(cat: TriggerVariables | null, event?: string): boolean {
  if (!event) return false
  return Boolean(cat?.lifecycle?.find((e) => e.event === event)?.agent_scoped)
}
export function eventTakesToolMatcher(event?: string): boolean {
  return event === 'PreToolUse' || event === 'PostToolUse'
}

export const ACTION_ICON: Record<string, LucideIcon> = {
  bash: TerminalSquare, 'run-script': FileCode2, webhook: Webhook,
  notify: Bell, 'send-message': MessageSquare, 'create-task': ListPlus, 'invoke-agent': Users,
  'run-prompt': Bot, 'run-workflow': Workflow,
  'self-remediation': Wrench,
}
export function actionIcon(provider?: string): LucideIcon { return ACTION_ICON[provider ?? ''] ?? Zap }

export function actionIsSendCapable(provider?: string): boolean {
  if (!provider) return false
  return provider === 'send-message' || provider.startsWith('send-')
}

const ACTION_LABEL: Record<string, string> = {
  bash: 'Bash', 'run-script': 'Script', webhook: 'Webhook',
  notify: 'Notify', 'send-message': 'Send Message', 'create-task': 'Create Task',
  'invoke-agent': 'Invoke Agent', 'run-prompt': 'Run Prompt', 'run-workflow': 'Run Workflow',
  'self-remediation': 'Self-Remediation',
}
export function actionLabel(provider?: string): string {
  if (!provider) return 'Action'
  return ACTION_LABEL[provider] ?? (provider.charAt(0).toUpperCase() + provider.slice(1).replace(/-/g, ' '))
}

export interface Trigger {
  kind: TriggerKind
  id: string
  rawId: string
  name: string
  enabled: boolean
  whenLabel: string
  whenIcon: LucideIcon
  whenTone: string
  actionLabel: string
  actionIcon: LucideIcon
  actionProvider?: string
  lastRunTs: number | null
  lastStatus: string | null
  state?: string | null
  health?: string | null
  lastError?: string | null
  runCount: number | null
  usedBy: string[]
  blocking?: boolean
  enforcement?: HookEnforcement
  storeKind?: string
  author?: string
  readOnly?: boolean
  broken?: string[]
  warnings?: string[]
  schedule?: ScheduleJob
  hook?: HookItem
  store?: WireTrigger
  eventPattern?: string
  eventMatcher?: string
  event?: WireTrigger
}

export function scheduleToTrigger(j: ScheduleJob): Trigger {
  const km = schedKindMeta(deriveKind(j))
  const mm = schedModeMeta(deriveMode(j))
  const provider = j.action?.provider
  const metadata = j as ScheduleJob & { state?: string | null; health?: string | null }
  return {
    kind: 'schedule', id: `schedule:${j.id}`, rawId: j.id, name: j.name || j.id, enabled: j.enabled,
    whenLabel: j.schedule, whenIcon: km.icon, whenTone: km.tone,
    actionLabel: provider ? actionLabel(provider) : mm.label,
    actionIcon: provider ? actionIcon(provider) : mm.icon,
    actionProvider: provider,
    lastRunTs: j.last_run_ts ?? null,
    lastStatus: j.last_run_status || (j.last_run_ts ? j.last_status : null) || null,
    state: metadata.state ?? null, health: metadata.health ?? j.last_status ?? null,
    lastError: j.last_error ?? null,
    runCount: null, usedBy: [],
    schedule: j,
    broken: j.broken ?? [], warnings: j.warnings ?? [],
    author: j.author, readOnly: j.read_only === true,
  }
}
function humanizeEvent(event: string): string {
  if (!event) return ''
  const spaced = event.replace(/([a-z])([A-Z])/g, '$1 $2')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase()
}
export function hookToTrigger(h: HookItem): Trigger {
  return {
    kind: 'lifecycle', id: `lifecycle:${h.id}`, rawId: h.id, name: h.name, enabled: h.enabled,
    whenLabel: humanizeEvent(h.event), whenIcon: Anchor, whenTone: 'var(--color-primary)',
    actionLabel: actionLabel(h.provider), actionIcon: actionIcon(h.provider), actionProvider: h.provider,
    lastRunTs: h.last_run || null, lastStatus: h.last_status || null, runCount: h.run_count, usedBy: h.used_by,
    blocking: h.blocking, enforcement: h.enforcement,
    schedule: undefined, hook: h,
  }
}

export function storeToTrigger(t: WireTrigger): Trigger {
  const km = storeKindMeta(t.store_kind)
  const provider = t.action?.provider
  return {
    kind: 'store', id: t.id, rawId: t.raw_id, name: t.name || t.raw_id, enabled: t.enabled,
    whenLabel: km.label, whenIcon: km.icon, whenTone: 'var(--color-primary)',
    actionLabel: provider ? actionLabel(provider) : 'Action',
    actionIcon: provider ? actionIcon(provider) : Zap,
    actionProvider: provider,
    lastRunTs: t.last_run_ts ?? null, lastStatus: t.last_run_status ?? null,
    state: t.state ?? null, health: t.health ?? null, lastError: t.last_error ?? null,
    runCount: t.run_count ?? null, usedBy: [],
    storeKind: t.store_kind, broken: t.broken ?? [], warnings: t.warnings ?? [], store: t,
    author: t.author, readOnly: t.read_only === true,
  }
}

export function eventToTrigger(t: WireTrigger): Trigger {
  const pm = eventPatternMeta(t.pattern)
  const provider = t.action?.provider
  return {
    kind: 'event', id: t.id, rawId: t.raw_id || t.id.replace(/^event:/, ''), name: t.name || t.id, enabled: t.enabled,
    whenLabel: pm.label, whenIcon: eventSourceIcon(pm.source), whenTone: 'var(--color-secondary)',
    actionLabel: provider ? actionLabel(provider) : 'Action',
    actionIcon: provider ? actionIcon(provider) : Zap,
    actionProvider: provider,
    lastRunTs: t.last_run_ts ?? t.last_fired_at ?? null,
    lastStatus: t.last_run_status ?? null, state: t.state ?? null,
    health: t.health ?? null, lastError: t.last_error ?? null,
    runCount: t.fire_count ?? null, usedBy: [],
    eventPattern: t.pattern, eventMatcher: eventMatcherValue(t, pm.matcher), event: t,
    author: t.author, readOnly: t.read_only === true,
  }
}

export interface TriggerStatusMeta extends StatusMeta { reason: string }

export function triggerStatusMeta(trigger: Trigger): TriggerStatusMeta {
  const lifecycle = triggerHealthMeta(trigger.health, trigger.state)
  const stopped = trigger.state && trigger.state !== 'active'
  const unhealthy = trigger.health && trigger.health !== 'ok' && trigger.health !== 'success'
  const meta = stopped || unhealthy
    ? lifecycle
    : statusMeta(trigger.lastRunTs || trigger.lastStatus ? trigger.lastStatus : null)
  return { ...meta, reason: trigger.lastError || '' }
}

export function eventMatcherValue(t: WireTrigger, field: EventMatcherField): string {
  if (!field) return ''
  return String((t as unknown as Record<string, unknown>)[field] ?? '')
}

export function relPast(ts?: number | string | null): string {
  const t = epochSeconds(ts)
  if (t == null) return 'never'
  const s = Date.now() / 1000 - t
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
