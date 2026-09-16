import type { ChatSession, InboxItem, InboxItemKind, InboxItemStatus, PendingApproval } from './api'

export const LANES = ['needs-approval', 'your-turn', 'working', 'idle'] as const
export type Lane = (typeof LANES)[number]

export type AttentionInput = Pick<
  InboxItem,
  'id' | 'item_kind' | 'status' | 'created_at' | 'ts' | 'message' | 'context_summary' | 'sender_name' | 'channel_name' | 'refs'
>

export type ApprovalInput = Pick<PendingApproval, 'id' | 'source' | 'tool' | 'tool_purpose' | 'session' | 'ts'>

export type ActivityInput = Pick<ChatSession, 'key' | 'title' | 'running' | 'stopping' | 'pending_approval'>

export interface LaneCard {
  key: string
  lane: Lane
  origin: 'approval' | 'inbox' | 'session'
  id: string
  title: string
  subtitle?: string
  at: number | null
  refs?: Record<string, unknown>
}

const BASE_LANE: Record<InboxItemKind, Lane | null> = {
  message: null,
  mention: null,
  email: null,
  agent_request: 'your-turn',
  proposal: 'your-turn',
  needs_input: 'your-turn',
  digest: 'idle',
  system: 'idle',
  user_note: 'your-turn',
}

const STATUS_OPEN: Record<InboxItemStatus, boolean> = {
  pending: true,
  seen: true,
  sent: false,
  handled: false,
  dismissed: false,
  filtered: false,
}

export const KNOWN_KINDS = Object.keys(BASE_LANE) as InboxItemKind[]

export function isKnownKind(kind: unknown): kind is InboxItemKind {
  return typeof kind === 'string' && Object.prototype.hasOwnProperty.call(BASE_LANE, kind)
}

const OLDEST_FIRST: ReadonlySet<Lane> = new Set<Lane>(['needs-approval', 'your-turn'])

function timeOf(item: AttentionInput): number | null {
  if (typeof item.created_at === 'number' && Number.isFinite(item.created_at)) return item.created_at
  if (typeof item.ts === 'string' && item.ts.trim() !== '') {
    const parsed = Number.parseFloat(item.ts)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

function mirroredApprovalId(item: AttentionInput): string {
  const raw = item.refs?.approval
  return typeof raw === 'string' && raw !== '' ? raw : ''
}

export function laneFor(item: AttentionInput): Lane | null {
  if (item === null || typeof item !== 'object') return null

  const kind = item.item_kind === undefined || item.item_kind === null ? 'message' : item.item_kind

  if (!isKnownKind(kind)) return null

  const status = item.status
  if (typeof status === 'string' && Object.prototype.hasOwnProperty.call(STATUS_OPEN, status)) {
    if (!STATUS_OPEN[status as InboxItemStatus]) return null
  }

  const base = BASE_LANE[kind]
  if (base === null) return null

  if (kind === 'agent_request' && mirroredApprovalId(item) !== '') return 'needs-approval'
  return base
}

function firstLine(text: unknown): string {
  if (typeof text !== 'string') return ''
  const trimmed = text.trim()
  if (trimmed === '') return ''
  const nl = trimmed.indexOf('\n')
  return nl === -1 ? trimmed : trimmed.slice(0, nl)
}

function sortLane(lane: Lane, cards: LaneCard[]): LaneCard[] {
  const ascending = OLDEST_FIRST.has(lane)
  return cards.sort((a, b) => {
    if (a.at === null && b.at === null) return a.key < b.key ? -1 : a.key > b.key ? 1 : 0
    if (a.at === null) return 1
    if (b.at === null) return -1
    if (a.at !== b.at) return ascending ? a.at - b.at : b.at - a.at
    return a.key < b.key ? -1 : a.key > b.key ? 1 : 0
  })
}

function emptyLanes(): Record<Lane, LaneCard[]> {
  const out = {} as Record<Lane, LaneCard[]>
  for (const lane of LANES) out[lane] = []
  return out
}

export function toLanes(
  items: AttentionInput[],
  approvals: ApprovalInput[],
  activity: ActivityInput[] = [],
): Record<Lane, LaneCard[]> {
  const out = emptyLanes()

  const approvalIds = new Set<string>()
  for (const a of Array.isArray(approvals) ? approvals : []) {
    if (a === null || typeof a !== 'object') continue
    const id = typeof a.id === 'string' ? a.id : ''
    if (id === '') continue
    approvalIds.add(id)
    out['needs-approval'].push({
      key: `approval:${id}`,
      lane: 'needs-approval',
      origin: 'approval',
      id,
      title: firstLine(a.tool) || 'a tool',
      subtitle: firstLine(a.tool_purpose) || firstLine(a.session) || undefined,
      at: typeof a.ts === 'number' && Number.isFinite(a.ts) ? a.ts : null,
    })
  }

  for (const item of Array.isArray(items) ? items : []) {
    if (item === null || typeof item !== 'object') continue
    const lane = laneFor(item)
    if (lane === null) continue
    const id = typeof item.id === 'string' ? item.id : ''
    if (id === '') continue

    const mirrored = mirroredApprovalId(item)
    if (mirrored !== '' && approvalIds.has(mirrored)) continue

    out[lane].push({
      key: `inbox:${id}`,
      lane,
      origin: 'inbox',
      id,
      title: firstLine(item.message) || firstLine(item.context_summary) || '(no message)',
      subtitle: firstLine(item.sender_name) || firstLine(item.channel_name) || undefined,
      at: timeOf(item),
      refs: item.refs,
    })
  }

  for (const s of Array.isArray(activity) ? activity : []) {
    if (s === null || typeof s !== 'object') continue
    const key = typeof s.key === 'string' ? s.key : ''
    if (key === '') continue
    if (s.running !== true && s.stopping !== true) continue
    out['working'].push({
      key: `session:${key}`,
      lane: 'working',
      origin: 'session',
      id: key,
      title: firstLine(s.title) || key,
      subtitle: s.stopping === true ? 'stopping' : 'running',
      at: null,
    })
  }

  for (const lane of LANES) sortLane(lane, out[lane])
  return out
}
