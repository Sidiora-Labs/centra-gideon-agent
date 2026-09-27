import type { WsMessage } from '../../../../console/src/shared/data/socketTransport'

const RELEVANT_TYPES = new Set([
  'approval', 'approval_resolved', 'notification', 'notification_ack', 'notification_unack',
  'notification_removed', 'inbox_new_item', 'inbox_item_updated',
])
const DEDUPE_WINDOW_MS = 1_000
export const ACTIVITY_EVENT_RETENTION = 128

function eventKey(message: WsMessage): string {
  const data = message.data
  if (message.type === 'refresh') {
    const kinds = Array.isArray(data.kinds) ? data.kinds.filter((kind): kind is string => typeof kind === 'string').sort() : []
    return `${message.type}:${kinds.join(',')}`
  }
  const identity = data.id ?? data.ts ?? data.session ?? data.inbox_item
  const revision = data.revision ?? data.status ?? data.acked
  if (typeof identity === 'string' || typeof identity === 'number') {
    return `${message.type}:${String(identity)}:${String(revision ?? '')}`
  }
  return `${message.type}:${JSON.stringify(data).slice(0, 512)}`
}

export function isActivityEventHint(message: WsMessage): boolean {
  if (RELEVANT_TYPES.has(message.type)) return true
  if (message.type === 'refresh') {
    return Array.isArray(message.data.kinds)
      && message.data.kinds.some(kind => kind === 'crons' || kind === 'history')
  }
  return false
}

export class ActivityEventWindow {
  private readonly received = new Map<string, number>()

  accept(message: WsMessage, now = Date.now()): boolean {
    if (!isActivityEventHint(message)) return false
    for (const [key, at] of this.received) {
      if (now - at >= DEDUPE_WINDOW_MS) this.received.delete(key)
    }
    const key = eventKey(message)
    const previous = this.received.get(key)
    if (previous !== undefined && now - previous < DEDUPE_WINDOW_MS) return false
    this.received.delete(key)
    this.received.set(key, now)
    while (this.received.size > ACTIVITY_EVENT_RETENTION) {
      const oldest = this.received.keys().next().value as string | undefined
      if (oldest === undefined) break
      this.received.delete(oldest)
    }
    return true
  }

  get size(): number { return this.received.size }
}
