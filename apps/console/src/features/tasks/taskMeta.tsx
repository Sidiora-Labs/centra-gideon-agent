import { Circle, CircleDot, CircleSlash, CheckCircle2, XCircle, CircleDashed, ListChecks, type LucideIcon } from 'lucide-react'
import type { ExitCriterion } from '../../shared/data/api'

export const ListChecksLike = ListChecks
export function isExitComplete(criterion: ExitCriterion): boolean {
  return criterion.status === undefined ? Boolean(criterion.met) : criterion.status === 'complete'
}
export function exitDoneCount(items: ExitCriterion[] = []): number {
  let completed = 0
  for (const criterion of items) if (isExitComplete(criterion)) completed++
  return completed
}

export interface StatusMeta { key: string; label: string; icon: LucideIcon; tone: string }

export const STATUSES: StatusMeta[] = [
  { key: 'open', label: 'Not started', icon: Circle, tone: 'var(--color-on-surface-low)' },
  { key: 'in_progress', label: 'In progress', icon: CircleDot, tone: 'var(--color-info)' },
  { key: 'blocked', label: 'Blocked', icon: CircleSlash, tone: 'var(--color-warn)' },
  { key: 'done', label: 'Completed', icon: CheckCircle2, tone: 'var(--color-ok)' },
  { key: 'cancelled', label: 'Cancelled', icon: XCircle, tone: 'var(--color-on-surface-low)' },
  { key: 'skipped', label: 'Skipped', icon: CircleDashed, tone: 'var(--color-on-surface-low)' },
]
export interface PriorityMeta { key: string; label: string; tone: string }
export const PRIORITIES: PriorityMeta[] = [
  { key: 'critical', label: 'Critical', tone: 'var(--color-danger)' },
  { key: 'high', label: 'High', tone: 'var(--color-warn)' },
  { key: 'medium', label: 'Medium', tone: 'var(--color-info)' },
  { key: 'low', label: 'Low', tone: 'var(--color-on-surface-low)' },
  { key: 'trivial', label: 'Trivial', tone: 'var(--color-on-surface-low)' },
]

const statuses = new Map(STATUSES.map(entry => [entry.key, entry]))
const priorities = new Map(PRIORITIES.map(entry => [entry.key, entry]))
const muted = 'var(--color-on-surface-low)'
export const TERMINAL = new Set(['done', 'cancelled', 'skipped'])
export function statusMeta(key?: string): StatusMeta {
  return statuses.get(key ?? '') ?? { key: key ?? '', label: key ?? 'Unknown', icon: Circle, tone: muted }
}
export function priorityMeta(key?: string): PriorityMeta {
  return priorities.get(key ?? '') ?? { key: key ?? '', label: key ?? '—', tone: muted }
}
export function signalPriority(key?: string): PriorityMeta | null {
  return key && key !== 'medium' ? priorityMeta(key) : null
}
const blockReasons = new Map([
  ['auto', { label: 'Waiting on a prerequisite', hint: 'Unblocks itself when the task it depends on is done or cancelled.' }],
  ['manual', { label: 'Blocked by you', hint: 'Not waiting on any tracked task — it stays blocked until you unblock it.' }],
])
export function blockKindMeta(kind?: string): { label: string; hint: string } | null {
  const reason = blockReasons.get(kind ?? '')
  return reason ? { ...reason } : null
}
export function SoonTag() {
  return <span data-type="caption" className="rounded-md border border-outline-variant/30 bg-surface-high px-1.5 py-0.5 text-on-surface-low uppercase tracking-wide" title="Designed ahead of the backend — not saved yet">soon</span>
}
const relativeUnits = [{ boundary: 3600, seconds: 60, label: 'm' }, { boundary: 86400, seconds: 3600, label: 'h' }, { boundary: 604800, seconds: 86400, label: 'd' }]
export function relTime(iso?: string): string {
  if (!iso) return ''
  const timestamp = Date.parse(iso)
  if (!Number.isFinite(timestamp)) return ''
  const elapsed = (Date.now() - timestamp) / 1000
  if (elapsed >= 0 && elapsed < 60) return 'just now'
  if (elapsed >= 0) for (const unit of relativeUnits) if (elapsed < unit.boundary) return `${Math.floor(elapsed / unit.seconds)}${unit.label} ago`
  return new Date(timestamp).toLocaleDateString()
}
export function parseDueDate(due: string): number {
  const text = due.trim()
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return Date.parse(due)
  const [year, month, day] = text.split('-').map(Number)
  const date = new Date(year, month - 1, day)
  return date.getMonth() + 1 === month && date.getDate() === day ? date.getTime() : NaN
}
function calendarDay(timestamp: number): number {
  const date = new Date(timestamp)
  return Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86400000
}
export function dueMeta(due?: string): { label: string; tone: string } | null {
  if (!due) return null
  const timestamp = parseDueDate(due)
  if (!Number.isFinite(timestamp)) return { label: due, tone: muted }
  const remaining = calendarDay(timestamp) - calendarDay(Date.now())
  if (remaining < 0) return { label: `${-remaining}d overdue`, tone: 'var(--color-danger)' }
  if (remaining < 2) return { label: remaining === 0 ? 'Due today' : 'Due tomorrow', tone: 'var(--color-warn)' }
  if (remaining <= 7) return { label: `Due in ${remaining}d`, tone: 'var(--color-on-surface-var)' }
  return { label: new Date(timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }), tone: muted }
}
