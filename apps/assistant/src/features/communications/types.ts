import type { OwnerScope } from '../../shared/auth.web'

export type CommunicationProviderKind =
  | 'mail-mirror'
  | 'calendar-source'
  | 'people'
  | 'teams'
  | 'signal-archive'
  | 'beeper'
  | 'telegram'
  | 'inbox'
  | 'notification'
  | 'approval'
  | 'live-mailbox'

export type CommunicationSourceKind =
  | 'mail-mirror-message'
  | 'calendar-event'
  | 'person'
  | 'channel-message'
  | 'inbox-item'
  | 'notification'
  | 'approval'
  | 'outbound-email-draft'

export type ConnectionReadiness =
  | 'configured'
  | 'connected'
  | 'read_only'
  | 'expired'
  | 'unavailable'
  | 'importing'
  | 'failed'

export type ContentFreshness = 'current' | 'stale' | 'imported' | 'unknown'

export type ProviderItemIdentity = Readonly<{
  ownerScopeKey: OwnerScope['cacheKey']
  accountId: string
  providerKind: CommunicationProviderKind
  sourceKind: CommunicationSourceKind
  nativeId: string
}>

export type CommunicationItem<T> = Readonly<{
  identity: ProviderItemIdentity
  readiness: ConnectionReadiness
  freshness: ContentFreshness
  allowedActions: readonly string[]
  value: T
}>

export type ReadSnapshot<T> = Readonly<{
  value: T
  freshness: ContentFreshness
  error?: string
}>

export type MirrorAccount = Readonly<{
  id: string
  name: string
  kind: 'maildir' | 'mbox' | 'imap'
  alias: 'custom' | 'gmail' | 'outlook'
  owner_email: string
  revision: number
  sync?: Readonly<{ state: string; coverage?: string }>
}>

export type MirrorMessage = Readonly<{
  external_id: string
  direction: 'inbound' | 'outbound'
  sender: string
  to: readonly string[]
  subject: string
  body: string
  occurred_at: string
  thread_id: string
  source_digest: string
}>

export type CalendarSource = Readonly<{
  id: string
  name: string
  kind: 'ics' | 'google' | 'outlook'
  calendar_id: string
  timezone: string
  revision: number
  sync?: Readonly<{ state: string; coverage?: string }>
}>

export type CalendarDaily = Readonly<Record<string, unknown>>
export type PersonRecord = Readonly<Record<string, unknown> & { id: string; revision: number }>
export type ChannelSource = Readonly<Record<string, unknown> & { id?: string; source_id?: string }>
export type InboxRecord = Readonly<Record<string, unknown> & { id: string }>
export type NotificationRecord = Readonly<Record<string, unknown> & { ts: string }>
export type ApprovalRecord = Readonly<Record<string, unknown> & { id: string }>

export type OutboundEmailDraft = Readonly<Record<string, unknown> & {
  id: string
  account_id: string
  revision: number
  content_sha256: string
  state: string
}>

export type LiveMailboxAvailability = Readonly<{
  readiness: 'unavailable'
  reason: 'no-registered-live-mailbox-read-route'
  items: readonly []
}>

export function providerItemKey(identity: ProviderItemIdentity): string {
  if (!identity.ownerScopeKey || !identity.accountId || !identity.nativeId) {
    throw new TypeError('Owner scope, native account ID and provider item ID are required')
  }
  return JSON.stringify([
    identity.ownerScopeKey,
    identity.accountId,
    identity.providerKind,
    identity.sourceKind,
    identity.nativeId,
  ])
}

export function connectionReadiness(serverState: unknown): ConnectionReadiness {
  switch (serverState) {
    case 'connected': return 'connected'
    case 'read_only':
    case 'read-only': return 'read_only'
    case 'expired': return 'expired'
    case 'unavailable': return 'unavailable'
    case 'importing': return 'importing'
    case 'failed':
    case 'error': return 'failed'
    default: return 'configured'
  }
}
