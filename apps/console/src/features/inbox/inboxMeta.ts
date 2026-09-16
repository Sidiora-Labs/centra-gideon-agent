import { Reply, Info, BellOff, CheckCircle2, Send, XCircle, Inbox as InboxIcon, AlertTriangle, ShieldQuestion, Eye, Filter, MessageSquare, AtSign, Mail, HelpCircle, Lightbulb, Newspaper, Settings2, StickyNote, UserCheck } from 'lucide-react'
import { epochSeconds } from '../../shared/data/epoch'
import type { LucideIcon } from 'lucide-react'
import type { InboxClassification, InboxConfidence, InboxItemStatus, InboxItemKind, InboxItem } from '../../shared/data/api'

function resolveMeta<Row extends { key: string }>(rows: Row[], key: string | undefined, fallback: number): Row {
  const index = rows.map(row => row.key).indexOf(key ?? '')
  return rows[index < 0 ? fallback : index]
}

export interface ClassMeta { key: InboxClassification; label: string; tone: string; icon: LucideIcon }
export const CLASSIFICATIONS: ClassMeta[] = [
  { key: 'needs_reply', label: 'Needs reply', tone: 'var(--color-info)', icon: Reply },
  { key: 'fyi', label: 'FYI', tone: 'var(--color-on-surface-low)', icon: Info },
  { key: 'noise', label: 'Noise', tone: 'var(--color-on-surface-low)', icon: BellOff },
]
export function classMeta(c?: string): ClassMeta {
  return resolveMeta(CLASSIFICATIONS, c, 0)
}

export interface ConfMeta { key: InboxConfidence; label: string; tone: string; icon: LucideIcon }
export const CONFIDENCES: ConfMeta[] = [
  { key: 'high', label: 'High confidence', tone: 'var(--color-ok)', icon: CheckCircle2 },
  { key: 'needs_review', label: 'Needs review', tone: 'var(--color-warn)', icon: ShieldQuestion },
  { key: 'escalate', label: 'Escalate', tone: 'var(--color-danger)', icon: AlertTriangle },

  { key: 'user', label: 'Set by you', tone: 'var(--color-info)', icon: UserCheck },
]
export function confMeta(c?: string): ConfMeta {
  return resolveMeta(CONFIDENCES, c, 1)
}

export interface StatusMeta { key: InboxItemStatus; label: string; tone: string; icon: LucideIcon }
export const STATUSES: StatusMeta[] = [
  { key: 'pending', label: 'Pending', tone: 'var(--color-info)', icon: InboxIcon },

  { key: 'seen', label: 'Seen', tone: 'var(--color-on-surface-low)', icon: Eye },
  { key: 'sent', label: 'Replied', tone: 'var(--color-ok)', icon: Send },
  { key: 'handled', label: 'Handled', tone: 'var(--color-ok)', icon: CheckCircle2 },
  { key: 'dismissed', label: 'Dismissed', tone: 'var(--color-on-surface-low)', icon: XCircle },

  { key: 'filtered', label: 'Filtered', tone: 'var(--color-warn)', icon: Filter },
]
export function statusMeta(s?: string): StatusMeta {
  return resolveMeta(STATUSES, s, 0)
}

export const OPEN_STATUSES: InboxItemStatus[] = ['pending', 'seen']
export function isOpen(s?: string): boolean {
  return OPEN_STATUSES.some(status => status === (s || 'pending'))
}

export interface KindMeta { key: InboxItemKind; label: string; tone: string; icon: LucideIcon }
export const ITEM_KINDS: KindMeta[] = [
  { key: 'message', label: 'Messages', tone: 'var(--color-primary)', icon: MessageSquare },
  { key: 'mention', label: 'Mentions', tone: 'var(--color-primary)', icon: AtSign },
  { key: 'email', label: 'Email', tone: 'var(--color-primary)', icon: Mail },
  { key: 'needs_input', label: 'Needs you', tone: 'var(--color-warn)', icon: HelpCircle },
  { key: 'agent_request', label: 'Agent requests', tone: 'var(--color-warn)', icon: ShieldQuestion },
  { key: 'proposal', label: 'Proposals', tone: 'var(--color-info)', icon: Lightbulb },
  { key: 'digest', label: 'Digests', tone: 'var(--color-on-surface-low)', icon: Newspaper },
  { key: 'system', label: 'System', tone: 'var(--color-on-surface-low)', icon: Settings2 },

  { key: 'user_note', label: 'Notes', tone: 'var(--color-info)', icon: StickyNote },
]
export function kindMeta(k?: string): KindMeta {
  return resolveMeta(ITEM_KINDS, k, 0)
}

export const NON_CHANNEL_ITEM_KINDS: InboxItemKind[] = [
  'agent_request', 'proposal', 'needs_input', 'digest', 'system',

  'user_note',
]

type ItemReference = Pick<InboxItem, 'refs'>
function destination(item: ItemReference): { path: string; label: string } {
  const refs = item.refs ?? {}
  const targets = [
    { id: refs.loop, path: `${refs.loop_kind === 'code' ? 'code' : 'loops'}/${refs.loop}`, label: 'Go to loop' },
    { id: refs.session, path: `chat/${encodeURIComponent(refs.session || '')}`, label: 'Go to chat' },
    { id: refs.workflow, path: `workflows/${refs.workflow}`, label: 'Go to workflow' },
    { id: refs.artifact, path: `artifacts/${encodeURIComponent(refs.artifact || '')}`, label: 'Open the report' },
  ]
  return targets.find(target => Boolean(target.id)) ?? { path: '', label: 'Go to source' }
}
export function refTarget(item: ItemReference): string { return destination(item).path }
export function refLabel(item: ItemReference): string { return destination(item).label }

export function channelLabel(item: Pick<InboxItem, 'channel' | 'channel_name'>): string {
  const label = item.channel_name || item.channel || ''
  if (!label || label === 'DM' || label[0] === '@') return label
  return '#' + (label[0] === '#' ? label.slice(1) : label)
}
export function sourceLabel(source?: string): string {
  return source && source !== 'native' ? source : 'agent'
}
export function relPast(timestamp?: number | string | null): string {
  const seconds = epochSeconds(timestamp)
  if (seconds == null) return ''
  const elapsed = Date.now() / 1000 - seconds
  const scale = [{ before: 60, size: 1, unit: '' }, { before: 3600, size: 60, unit: 'm' }, { before: 86400, size: 3600, unit: 'h' }, { before: Infinity, size: 86400, unit: 'd' }].find(step => elapsed < step.before)!
  return scale.unit ? `${Math.floor(elapsed / scale.size)}${scale.unit} ago` : 'just now'
}
