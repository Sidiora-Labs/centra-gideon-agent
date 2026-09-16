import { Bell, BellRing, CheckCircle2, Clock, Webhook, Bot, HeartPulse, Info, AlertTriangle, Target, XCircle, Newspaper, MessageSquare, MessageCircle, Activity, Lightbulb, Archive, Route, HelpCircle, ShieldQuestion, ShieldOff, RefreshCw, Receipt, UserRound, StickyNote } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { NotificationItem } from '../../shared/data/api'

export interface KindMeta { label: string; icon: LucideIcon; tone: string }
const KINDS: Record<string, KindMeta> = {
  cron: { label: 'Scheduled job result', icon: Clock, tone: 'var(--color-info)' },
  schedule: { label: 'Scheduled job result', icon: Clock, tone: 'var(--color-info)' },
  result: { label: 'Scheduled job result', icon: CheckCircle2, tone: 'var(--color-ok)' },
  hook: { label: 'Trigger fired', icon: Webhook, tone: 'var(--color-primary)' },
  fired: { label: 'Trigger fired', icon: Webhook, tone: 'var(--color-primary)' },
  agent: { label: 'Agent message', icon: Bot, tone: 'var(--color-primary)' },
  subagent: { label: 'Subagent update', icon: Bot, tone: 'var(--color-primary)' },
  message: { label: 'Agent message', icon: MessageSquare, tone: 'var(--color-on-surface-low)' },
  agent_request: { label: 'Agent request', icon: ShieldQuestion, tone: 'var(--color-warn)' },
  heartbeat: { label: 'Heartbeat', icon: HeartPulse, tone: 'var(--color-info)' },
  status: { label: 'Heartbeat', icon: HeartPulse, tone: 'var(--color-info)' },
  inbox_alert: { label: 'Inbox alert', icon: BellRing, tone: 'var(--color-warn)' },
  alert: { label: 'Inbox alert', icon: BellRing, tone: 'var(--color-warn)' },
  loop: { label: 'Loop progress', icon: Target, tone: 'var(--color-primary)' },
  complete: { label: 'Loop complete', icon: CheckCircle2, tone: 'var(--color-ok)' },

  failed: { label: 'Loop failed', icon: XCircle, tone: 'var(--color-danger)' },
  stalled: { label: 'Loop stalled or blocked', icon: AlertTriangle, tone: 'var(--color-warn)' },
  needs_input: { label: 'Loop needs your input', icon: HelpCircle, tone: 'var(--color-warn)' },
  progress: { label: 'Loop progress', icon: Activity, tone: 'var(--color-info)' },
  proposal: { label: 'Skill proposal', icon: Lightbulb, tone: 'var(--color-primary)' },

  autonomy_revocation: { label: 'Earned autonomy revoked', icon: ShieldOff, tone: 'var(--color-warn)' },

  learning_proposal: { label: 'Learning proposal', icon: Lightbulb, tone: 'var(--color-primary)' },
  planning_proposal: { label: 'Planning proposal', icon: Lightbulb, tone: 'var(--color-primary)' },
  digest: { label: 'Daily digest', icon: Newspaper, tone: 'var(--color-on-surface-low)' },

  usage_recap: { label: 'Monthly usage recap', icon: Receipt, tone: 'var(--color-on-surface-low)' },

  approval: { label: 'Approval needed', icon: ShieldQuestion, tone: 'var(--color-warn)' },

  research_finding: { label: 'Research report finding', icon: Newspaper, tone: 'var(--color-primary)' },

  report: { label: 'Identity report', icon: UserRound, tone: 'var(--color-primary)' },

  update: { label: 'App update available', icon: RefreshCw, tone: 'var(--color-primary)' },
  app_update: { label: 'App update available', icon: RefreshCw, tone: 'var(--color-primary)' },

  user_note: { label: 'Note you captured', icon: StickyNote, tone: 'var(--color-primary)' },
  session: { label: 'Session notice', icon: MessageCircle, tone: 'var(--color-on-surface-low)' },
  retire: { label: 'Retired a learned signal', icon: Archive, tone: 'var(--color-primary)' },
  feedback_retire: { label: 'Retired a learned signal', icon: Archive, tone: 'var(--color-primary)' },
  route_drift: { label: 'App route drift', icon: Route, tone: 'var(--color-primary)' },
  'app.route.drift': { label: 'App route drift', icon: Route, tone: 'var(--color-primary)' },
  success: { label: 'Success', icon: CheckCircle2, tone: 'var(--color-ok)' },
  warning: { label: 'System warning', icon: AlertTriangle, tone: 'var(--color-warn)' },
  error: { label: 'System error', icon: AlertTriangle, tone: 'var(--color-danger)' },
  info: { label: 'Notice', icon: Info, tone: 'var(--color-on-surface-low)' },
  generic: { label: 'Uncategorized', icon: Bell, tone: 'var(--color-on-surface-low)' },
}
export function kindMeta(kind: string): KindMeta {
  const known = Object.prototype.hasOwnProperty.call(KINDS, kind) ? KINDS[kind] : undefined
  return known || { label: kind || 'Notification', icon: Bell, tone: 'var(--color-primary)' }
}
export function toneChipBg(tone: string): string {
  return `color-mix(in srgb, ${tone} 16%, transparent)`
}
export function kindsPresent(items: NotificationItem[]): string[] {
  return Array.from(new Set(items.map(item => item.kind || 'info')))
}
export type Bucket = 'Today' | 'Yesterday' | 'Earlier'
export const BUCKET_ORDER: Bucket[] = ['Today', 'Yesterday', 'Earlier']
export function bucketOf(iso: string, now: number): Bucket {
  const stamp = Date.parse(iso)
  const midnight = new Date(now).setHours(0, 0, 0, 0)
  const thresholds: Array<[Bucket, number]> = [['Today', midnight], ['Yesterday', midnight - 86400_000]]
  return thresholds.find(([, start]) => stamp >= start)?.[0] ?? 'Earlier'
}
export function relTime(iso: string, now: number): string {
  const stamp = Date.parse(iso)
  if (Number.isNaN(stamp)) return ''
  const elapsed = Math.max(0, now - stamp) / 1000
  if (elapsed < 60) return 'just now'
  const divisor = elapsed < 3600 ? 60 : elapsed < 86400 ? 3600 : 86400
  const unit = divisor === 60 ? 'm' : divisor === 3600 ? 'h' : 'd'
  return `${Math.floor(elapsed / divisor)}${unit} ago`
}
export function clockTime(iso: string): string {
  const stamp = Date.parse(iso)
  return Number.isNaN(stamp) ? '' : new Date(stamp).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
export function firstLine(body: string, max = 120): string {
  let first = ''
  for (const candidate of (body || '').split('\n')) {
    if (!candidate.trim()) continue
    first = candidate
    break
  }
  return first.length <= max ? first : `${first.slice(0, max)}…`
}
