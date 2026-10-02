export interface HypermidCursor {
  epoch: number
  sequence: number
}

export interface HypermidScope {
  owner_id: string
  project_id: string
  workspace_id?: string
}

export type HypermidInspectionState = 'complete' | 'filtered' | 'stale' | 'rebuilding' | 'unavailable' | 'unreadable'

export interface HypermidTimelineItem {
  id: string
  cursor: HypermidCursor
  kind: 'user' | 'assistant' | 'tool' | 'memory' | 'cache' | 'compaction' | 'diagnostic' | 'error'
  occurred_at: string
  summary: string
  trace?: { trace_id: string; request_id?: string }
  detail_digest?: string
}

export interface HypermidTimelineSnapshot {
  session_id: string
  cursor: HypermidCursor
  items: HypermidTimelineItem[]
  gap?: { after: HypermidCursor; reason: string }
}

function cursorOrder(left: HypermidCursor, right: HypermidCursor): number {
  return left.epoch === right.epoch ? left.sequence - right.sequence : left.epoch - right.epoch
}

export function cursorKey(cursor: HypermidCursor): string {
  return `${cursor.epoch}:${cursor.sequence}`
}

export function adoptTimelineSnapshot(
  snapshot: HypermidTimelineSnapshot,
  replay: readonly HypermidTimelineItem[],
): HypermidTimelineSnapshot {
  const epoch = snapshot.cursor.epoch
  const ordered = new Map<string, HypermidTimelineItem>()
  for (const item of snapshot.items) {
    if (item.cursor.epoch === epoch && cursorOrder(item.cursor, snapshot.cursor) <= 0) {
      ordered.set(cursorKey(item.cursor), item)
    }
  }
  for (const item of replay) {
    if (item.cursor.epoch === epoch && cursorOrder(item.cursor, snapshot.cursor) > 0) {
      ordered.set(cursorKey(item.cursor), item)
    }
  }
  const items = [...ordered.values()].sort((left, right) => cursorOrder(left.cursor, right.cursor))
  const cursor = items.length > 0 && cursorOrder(items[items.length - 1].cursor, snapshot.cursor) > 0
    ? items[items.length - 1].cursor
    : snapshot.cursor
  return { ...snapshot, cursor, items }
}

export type HypermidInspectionFilters = Record<string, string | undefined>

export function inspectionQueryKey(collection: 'sessions' | 'memory' | 'caches', filters: HypermidInspectionFilters): string {
  const normalized = Object.entries(filters)
    .filter((entry): entry is [string, string] => Boolean(entry[1]))
    .sort(([left], [right]) => left.localeCompare(right))
  if (normalized.length === 0) return `hypermid:${collection}:complete`
  return `hypermid:${collection}:filtered:${normalized.map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`).join('&')}`
}

export interface ConflictDraft<T> {
  authoritative: T
  revision: string
  draft: T
  conflict?: { latest: T; latest_revision: string; message: string }
}

export function beginConflictDraft<T>(authoritative: T, revision: string): ConflictDraft<T> {
  return { authoritative, revision, draft: authoritative }
}

export function retainRejectedDraft<T>(
  state: ConflictDraft<T>,
  latest: T,
  latestRevision: string,
  message: string,
): ConflictDraft<T> {
  return { ...state, conflict: { latest, latest_revision: latestRevision, message } }
}

export function reloadConflict<T>(state: ConflictDraft<T>): ConflictDraft<T> {
  if (!state.conflict) return state
  return beginConflictDraft(state.conflict.latest, state.conflict.latest_revision)
}

export function reapplyConflict<T>(state: ConflictDraft<T>): ConflictDraft<T> {
  if (!state.conflict) return state
  return {
    authoritative: state.conflict.latest,
    revision: state.conflict.latest_revision,
    draft: state.draft,
  }
}
