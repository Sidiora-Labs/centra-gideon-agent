import { Repeat, CalendarClock, Calendar, Bot, FileCode2, TerminalSquare, CheckCircle2, XCircle, Circle, Rocket, Clock, ShieldAlert, PauseCircle } from 'lucide-react'
import { epochSeconds } from '../../shared/data/epoch'
import type { LucideIcon } from 'lucide-react'
import type { ScheduleJob, ScheduleKind, ScheduleExecMode } from '../../shared/data/api'

export interface KindMeta { key: ScheduleKind; label: string; icon: LucideIcon; tone: string; hint: string; soon?: boolean }
export const KINDS: KindMeta[] = [
  { key: 'every', label: 'Interval', icon: Repeat, tone: 'var(--color-info)', hint: 'Run every N minutes/hours/days.' },
  { key: 'cron', label: 'Cron', icon: CalendarClock, tone: 'var(--color-primary)', hint: 'Five-field cron expression (min hour dom month dow).' },
  { key: 'at', label: 'One-shot', icon: Calendar, tone: 'var(--color-warn)', hint: 'Fire once at a specific date & time.' },
]
export function kindMeta(k?: ScheduleKind): KindMeta { return KINDS.find((x) => x.key === k) ?? KINDS[0] }

export interface ModeMeta { key: ScheduleExecMode; label: string; icon: LucideIcon; tone: string; hint: string; soon?: boolean }
export const EXEC_MODES: ModeMeta[] = [
  { key: 'agent', label: 'Agent', icon: Bot, tone: 'var(--color-primary)', hint: 'An LLM agent runs your prompt each time.' },
  { key: 'script', label: 'Script', icon: FileCode2, tone: 'var(--color-info)', hint: 'Zero-token: run a Python entrypoint (path/to/file.py:func) under ~/.gideon/crons/.', soon: true },
  { key: 'command', label: 'Command', icon: TerminalSquare, tone: 'var(--color-ok)', hint: 'Zero-token: run a shell command in the sandbox.', soon: true },
]
export const OTHER_MODE: ModeMeta = {
  key: 'other', label: 'Action', icon: Rocket, tone: 'var(--color-on-surface-var)',
  hint: "This automation runs an action provider (a notification, a digest, a remediation) rather than an agent prompt.",
}
export function modeMeta(m?: ScheduleExecMode): ModeMeta {
  if (m === 'other') return OTHER_MODE
  return EXEC_MODES.find((x) => x.key === m) ?? EXEC_MODES[0]
}

export function scheduleWhenMet(d: { kind: ScheduleKind; cron: string; intervalValue: number; intervalUnit: string; at: string }): Record<string, unknown> {
  if (d.kind === 'cron') return { cron: d.cron.trim() }
  if (d.kind === 'every') return { every: intervalToSecs(d.intervalValue, d.intervalUnit) }
  const milliseconds = new Date(d.at).getTime()
  return { at: Number.isNaN(milliseconds) ? d.at : Math.floor(milliseconds / 1000) }
}

export function deriveKind(j: ScheduleJob): ScheduleKind {
  if (j.cron_expr) return 'cron'
  if (j.every_secs != null) return 'every'
  return 'at'
}
export function deriveMode(j: ScheduleJob): ScheduleExecMode {
  if (j.script) return 'script'
  if (j.command) return 'command'
  const provider = j.action?.provider || ''
  if (provider === 'run-script') return 'script'
  if (provider === 'bash') return 'command'
  if (!provider || provider === 'invoke-agent') return 'agent'
  return 'other'
}

export interface StatusMeta { label: string; tone: string; icon: LucideIcon }
export function statusMeta(s?: string | null): StatusMeta {
  if (s === 'ok' || s === 'success') return { label: 'ok', tone: 'var(--color-ok)', icon: CheckCircle2 }
  if (s === 'error' || s === 'failure') return { label: 'error', tone: 'var(--color-danger)', icon: XCircle }
  if (s === 'ran') return { label: 'ran', tone: 'var(--color-ok)', icon: CheckCircle2 }
  if (s === 'ran_late') return { label: 'ran late', tone: 'var(--color-warning)', icon: Clock }
  if (s === 'failed') return { label: 'failed', tone: 'var(--color-danger)', icon: XCircle }
  if (s === 'timeout') return { label: 'timed out', tone: 'var(--color-danger)', icon: Clock }
  if (s === 'launched') return { label: 'launched', tone: 'var(--color-info)', icon: Rocket }
  if (s === 'blocked_injection') return { label: 'blocked', tone: 'var(--color-danger)', icon: ShieldAlert }
  if (s && s.startsWith('skipped_')) return { label: s.slice(8).replace(/_/g, ' '), tone: 'var(--color-on-surface-low)', icon: PauseCircle }
  if (s === 'deferred') return { label: 'deferred', tone: 'var(--color-info)', icon: PauseCircle }
  if (s === 'refused') return { label: 'refused', tone: 'var(--color-warning)', icon: ShieldAlert }
  return { label: 'never run', tone: 'var(--color-on-surface-low)', icon: Circle }
}



export function isInertOutcome(s?: string | null): boolean {
  return Boolean(s) && String(s).startsWith('skipped_')
}


export function triggerHealthMeta(health?: string | null, state?: string | null): StatusMeta {
  if (state === 'quarantined') {
    return { label: 'quarantined', tone: 'var(--color-danger)', icon: ShieldAlert }
  }
  if (state === 'autopaused') return { label: 'autopaused', tone: 'var(--color-danger)', icon: XCircle }
  if (state === 'paused') return { label: 'paused', tone: 'var(--color-on-surface-low)', icon: PauseCircle }
  if (state === 'retired') return { label: 'retired', tone: 'var(--color-on-surface-low)', icon: Circle }
  if (state === 'parked' || health === 'parked') {
    return { label: 'parked', tone: 'var(--color-info)', icon: PauseCircle }
  }
  if (health === 'failing') return { label: 'failing', tone: 'var(--color-danger)', icon: XCircle }
  if (health === 'degraded') return { label: 'degraded', tone: 'var(--color-warning)', icon: Clock }
  if (health === 'ok') return { label: 'ok', tone: 'var(--color-ok)', icon: CheckCircle2 }
  return { label: '', tone: 'var(--color-on-surface-low)', icon: Circle }
}

export function lastRunMeta(runStatus?: string | null, health?: string | null): StatusMeta {
  if (health && health !== 'ok' && health !== 'success') {
    const hm = triggerHealthMeta(health)
    if (hm.label) return hm
    return statusMeta(health)
  }
  return statusMeta(runStatus || health)
}

export function relFuture(ts?: number | string | null): string {
  const t = epochSeconds(ts)
  if (t == null) return ''
  const s = t - Date.now() / 1000
  if (s < 0) return 'overdue'
  if (s < 60) return 'in <1m'
  if (s < 3600) return `in ${Math.floor(s / 60)}m`
  if (s < 86400) return `in ${Math.floor(s / 3600)}h`
  return `in ${Math.floor(s / 86400)}d`
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
export function absTime(ts?: number | string | null): string {
  const t = epochSeconds(ts)
  if (t == null) return ''
  return new Date(t * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function mdToPlain(s?: string | null): string {
  if (!s) return ''
  return s
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^[\s>]*#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/^\s*\|.*\|\s*$/gm, ' ')
    .replace(/[*_~]{1,3}([^*_~]+)[*_~]{1,3}/g, '$1')
    .replace(/[*_~`>#|-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

export const INTERVAL_UNITS: Array<{ key: string; label: string; secs: number }> = [
  { key: 'm', label: 'minutes', secs: 60 },
  { key: 'h', label: 'hours', secs: 3600 },
  { key: 'd', label: 'days', secs: 86400 },
]
export function secsToInterval(secs?: number | null): { value: number; unit: string } {
  const s = secs ?? 3600
  if (s % 86400 === 0) return { value: s / 86400, unit: 'd' }
  if (s % 3600 === 0) return { value: s / 3600, unit: 'h' }
  return { value: Math.max(1, Math.round(s / 60)), unit: 'm' }
}
export function intervalToSecs(value: number, unit: string): number {
  const u = INTERVAL_UNITS.find((x) => x.key === unit) ?? INTERVAL_UNITS[0]
  return Math.max(60, Math.round(value) * u.secs)
}

export const CRON_PRESETS: Array<{ label: string; expr: string }> = [
  { label: 'Hourly', expr: '0 * * * *' },
  { label: 'Daily 9am', expr: '0 9 * * *' },
  { label: 'Weekdays 9am', expr: '0 9 * * 1-5' },
  { label: 'Weekly Mon', expr: '0 9 * * 1' },
  { label: 'Monthly 1st', expr: '0 9 1 * *' },
]
