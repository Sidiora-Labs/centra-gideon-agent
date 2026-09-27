import type { OwnerScope } from '../../shared/auth.web'

export type CommunicationProviderKind =
  | 'mail-mirror' | 'calendar-source' | 'people' | 'teams' | 'signal-archive'
  | 'beeper' | 'telegram' | 'inbox' | 'notification' | 'approval' | 'live-mailbox'
export type CommunicationSourceKind =
  | 'mail-mirror-message' | 'calendar-event' | 'person' | 'channel-message'
  | 'inbox-item' | 'notification' | 'approval' | 'outbound-email-draft'
export type ConnectionReadiness = 'configured' | 'connected' | 'read_only' | 'expired' | 'unavailable' | 'importing' | 'failed'
export type ContentFreshness = 'current' | 'stale' | 'imported' | 'unknown'
export type JsonValue = null | boolean | number | string | readonly JsonValue[] | { readonly [key: string]: JsonValue }

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
export type ReadSnapshot<T> = Readonly<{ value: T; freshness: ContentFreshness; error?: string }>

export type SyncState = Readonly<{
  state: string
  coverage: string
  scope?: string
  start?: string
  end?: string
  captured_at?: string
  events?: number
  warnings?: readonly string[]
  source_digest?: string
  source_revision?: number
  review_coverage?: string
}>

export type MirrorAccount = Readonly<{
  id: string
  name: string
  kind: 'maildir' | 'mbox' | 'imap'
  alias: 'custom' | 'gmail' | 'outlook'
  owner_email: string
  host?: string
  username?: string
  credential_ref?: string
  auth_mode?: 'password' | 'xoauth2'
  inbox_folder?: string
  sent_folder?: string
  revision: number
  sync?: SyncState
}>
export type MirrorAccountInput = Readonly<{
  name: string
  kind: MirrorAccount['kind']
  alias?: MirrorAccount['alias']
  owner_email: string
  host?: string
  username?: string
  credential_ref?: string
  auth_mode?: 'password' | 'xoauth2'
  inbox_folder?: string
  sent_folder?: string
}>
export type MirrorMessageAttachment = Readonly<{
  filename: string
  content_type: string
  part_index?: number
  source_id?: string
  sha256: string
  size: number
  state: 'materialized' | 'unsupported_content'
  artifact_id: string | null
  artifact_uri?: string
}>
export type MirrorMessage = Readonly<{
  external_id: string
  thread_id: string
  occurred_at: string | null
  direction: 'inbound' | 'outbound'
  subject: string
  body: string
  sender: readonly string[]
  recipients: readonly string[]
  attachments: readonly MirrorMessageAttachment[]
  source_digest: string
}>
export type MirrorSyncResult = Readonly<{
  state: 'synced'
  seen: number
  coverage: string
  captured_at: string
  scope: string
  evidence_status: string
  external_delivery: 'not_performed'
}>
export type MirrorUploadResult = Readonly<{ source_digest: string; changed: boolean; bytes: number }>
export type MirrorUploadInput = Readonly<{ content: string; folder?: 'INBOX' | 'Sent' }>

export type CalendarSource = Readonly<{
  id: string
  name: string
  kind: 'ics' | 'google' | 'outlook'
  calendar_id: string
  credential_ref: string
  timezone: string
  revision: number
  sync: SyncState
  review_coverage?: string
}>
export type CalendarSourceInput = Readonly<{
  name: string
  kind: CalendarSource['kind']
  calendar_id?: string
  credential_ref?: string
  timezone?: string
}>
export type CalendarEvent = Readonly<{
  id: string
  uid: string
  title: string
  location: string
  start: string
  end: string
  all_day: boolean
  status: string
  recurrence_unexpanded: boolean
  source_id: string
  source_kind: CalendarSource['kind']
  recurring?: boolean
  recurrence_id?: string
  overridden?: boolean
}>
export type CalendarDaily = Readonly<{
  date: string
  timezone: string
  events: readonly CommunicationItem<CalendarEvent>[]
  sources: readonly CalendarSource[]
  coverage: 'unknown' | 'partial' | 'available_snapshot'
}>
export type CalendarUploadInput = Readonly<{
  revision: number
  content: string
  window_start: string
  window_end: string
}>
export type CalendarSyncInput = Readonly<{ start: string; end: string }>

export type PersonIdentity = Readonly<{ kind: 'email' | 'phone' | 'handle'; value: string }>
export type PersonRecord = Readonly<{
  id: string
  name: string
  notes: string
  ring: 'support' | 'core' | 'tribe' | 'village' | 'external'
  cadence_days: number
  identities: readonly PersonIdentity[]
  revision: number
}>
export type PersonInput = Readonly<{
  name: string
  notes?: string
  ring?: PersonRecord['ring']
  cadence_days?: number
  identities?: readonly PersonIdentity[]
}>
export type PersonTouchpoint = Readonly<{
  id: string
  person_id: string
  source: string
  external_id: string
  occurred_at: string
  direction: 'inbound' | 'outbound' | 'mutual'
  summary: string
}>
export type PersonCare = Readonly<{
  state: 'excluded' | 'missing' | 'overdue' | 'current'
  last_contact: string | null
  days_since: number | null
  days_overdue: number | null
}>
export type PersonDetail = Readonly<{
  person: PersonRecord
  touchpoints: readonly PersonTouchpoint[]
  care: PersonCare
  timezone: string
}>
export type TouchpointInput = Readonly<Omit<PersonTouchpoint, 'id' | 'person_id'>>

export type TeamsSource = Readonly<{
  id: string
  name: string
  owner_email: string
  credential_ref: string
  revision: number
  sync: SyncState
}>
export type TeamsSourceInput = Readonly<Omit<TeamsSource, 'id' | 'revision' | 'sync'>>
export type TeamsMessage = Readonly<{
  provenance_key: string
  provider: 'microsoft_graph'
  source_kind: string
  conversation_id: string
  team_id: string | null
  channel_id: string | null
  message_id: string
  reply_to_id: string | null
  sender: Readonly<{ id: string; name: string }>
  person_id: string | null
  direction: 'inbound' | 'outbound'
  created_at: string
  modified_at: string | null
  deleted_at: string | null
  etag: string
  body: string
  attachments: readonly Readonly<{ id: string; name: string; content_type: string }>[]
}>
export type TeamsSyncResult = Readonly<{
  state: 'synced'
  coverage: string
  captured_at: string
  messages_seen: number
  conversations_seen: number
  owner_graph_id: string
  next_action: string | null
}>
export type SignalArchiveMessage = Readonly<{
  external_id: string
  message_id: string
  conversation_id: string
  occurred_at: string | null
  direction: 'inbound' | 'outbound' | null
  body: string
  identity: PersonIdentity | null
  attachments: readonly Readonly<{ path: string; name: string; content_type: string; size: number | null }>[]
  limitations: readonly string[]
}>
export type SignalArchiveReceipt = Readonly<{
  source_account_id: string
  source_digest: string
  review_token: string
  coverage: string
  qualification: string
  messages: number
  inserted: number
  linked: number
  captured_at: string
}>
export type InboxActionResult = Readonly<{ ok: boolean }>
export type InboxApplyResult =
  | Readonly<{ ok: true; item: InboxRecord; [key: string]: JsonValue | InboxRecord }>
  | Readonly<{ ok: false; error: string; item?: InboxRecord; [key: string]: JsonValue | InboxRecord | undefined }>
export type NotificationActionResult = Readonly<{ ok: boolean; unread?: number; deleted?: boolean }>
export type UnavailableChannel = Readonly<{
  kind: 'beeper' | 'telegram'
  readiness: 'unavailable'
  reason: string
  items: readonly []
}>

export type OutboundEmailDraft = Readonly<{
  id: string
  request_key: string
  account_id: string
  account_revision: number
  credential_ref: string
  sender: string
  to: readonly string[]
  subject: string
  body: string
  source_message_id: string | null
  attachments: readonly Readonly<{
    artifact_id: string; version: number | null; name: string; mime: string; size: number; sha256: string
  }>[]
  message_id: string
  created_at: string
  updated_at: string
  state: 'draft' | 'approved' | 'sending' | 'accepted' | 'uncertain' | 'failed' | 'verified'
  provider_acceptance: string
  delivery: string
  verification: Readonly<Record<string, string | readonly Readonly<{ url: string; opened: boolean }>[]> | null>
  content_sha256: string
  fingerprint: string
  revision: number
  approved_at?: string
  accepted_at?: string
  transport_receipt?: Readonly<{ code: number; response: string }>
  error?: string
}>
export type OutboundDraftInput = Readonly<{
  request_key: string
  account_id: string
  to: readonly string[]
  subject?: string
  body: string
  source_message_id?: string
  attachments?: readonly Readonly<{ artifact_id: string; version?: number }>[]
}>
export type OutboundDraftItem = CommunicationItem<OutboundEmailDraft>

export type InboxRecord = Readonly<{
  id: string
  channel: string
  channel_name: string
  thread_ts: string | null
  message: string
  sender_id: string
  sender_name: string
  thread_context: readonly Readonly<Record<string, string>>[]
  classification: 'needs_reply' | 'fyi' | 'noise'
  draft: string
  confidence: 'high' | 'needs_review' | 'escalate' | 'user'
  status: 'pending' | 'seen' | 'sent' | 'dismissed' | 'handled' | 'filtered'
  created_at: number
  context_summary: string
  source: string
  can_reply: boolean
  reply_target: string
  favorited: boolean
  item_kind: string
  refs: Readonly<Record<string, JsonValue>>
  feedback_producers?: Readonly<Record<string, Readonly<{ producer_kind: string; producer_id: string }>>>
  owner: string
  owner_states: Readonly<Record<string, string>>
  ts: string
}>
export type InboxUpdate = Readonly<Partial<Pick<InboxRecord,
  'status' | 'draft' | 'classification' | 'confidence' | 'favorited'>>>
export type NotificationRecord = Readonly<{
  kind: string
  title: string
  body: string
  ts: string
  acked?: boolean
  withheld_reason?: string
  routed_to?: string
  [key: string]: JsonValue | undefined
}>
export type ApprovalRecord = Readonly<{
  id: string
  source: string
  tool: string
  tool_input: JsonValue
  tool_purpose: string
  session: string
  ts: number
}>

export type LiveMailboxAvailability = Readonly<{
  readiness: 'unavailable'
  reason: 'no-registered-live-mailbox-read-route'
  items: readonly []
}>

export type SelectionToken = Readonly<{ generation: number; accountId: string; providerKind: CommunicationProviderKind }>

export function providerItemKey(identity: ProviderItemIdentity): string {
  if (!identity.ownerScopeKey || !identity.accountId || !identity.nativeId) {
    throw new TypeError('Owner scope, native account ID and provider item ID are required')
  }
  return JSON.stringify([identity.ownerScopeKey, identity.accountId, identity.providerKind,
    identity.sourceKind, identity.nativeId])
}

export function connectionReadiness(serverState: unknown): ConnectionReadiness {
  switch (serverState) {
    case 'connected': return 'connected'
    case 'read_only':
    case 'read-only': return 'read_only'
    case 'expired': return 'expired'
    case 'revoked': return 'expired'
    case 'unavailable': return 'unavailable'
    case 'importing': return 'importing'
    case 'failed':
    case 'error': return 'failed'
    default: return 'configured'
  }
}

export function mirrorReadiness(account: MirrorAccount): ConnectionReadiness {
  if (account.sync?.state === 'failed') return 'failed'
  if (account.kind === 'imap' && account.sync?.state === 'synced') return 'connected'
  if (account.sync?.state === 'synced' || account.kind !== 'imap') return 'read_only'
  return 'configured'
}

export function calendarEventItem(scopeKey: OwnerScope['cacheKey'], source: CalendarSource,
  event: CalendarEvent): CommunicationItem<CalendarEvent> {
  return {
    identity: { ownerScopeKey: scopeKey, accountId: source.id, providerKind: 'calendar-source',
      sourceKind: 'calendar-event', nativeId: event.id },
    readiness: connectionReadiness(source.sync.state),
    freshness: source.review_coverage === 'available_snapshot' ? 'current' : 'unknown',
    allowedActions: source.kind === 'ics' ? ['open', 'read', 'import'] : ['open', 'read'],
    value: event,
  }
}

export function teamsMessageItem(scopeKey: OwnerScope['cacheKey'], source: TeamsSource,
  message: TeamsMessage): CommunicationItem<TeamsMessage> {
  return {
    identity: { ownerScopeKey: scopeKey, accountId: source.id, providerKind: 'teams',
      sourceKind: 'channel-message', nativeId: message.provenance_key },
    readiness: connectionReadiness(source.sync.state),
    freshness: source.sync.state === 'synced' ? 'current' : 'unknown',
    allowedActions: ['open', 'read'],
    value: message,
  }
}

export function signalArchiveMessageItem(scopeKey: OwnerScope['cacheKey'], sourceAccountId: string,
  message: SignalArchiveMessage): CommunicationItem<SignalArchiveMessage> {
  return {
    identity: { ownerScopeKey: scopeKey, accountId: sourceAccountId, providerKind: 'signal-archive',
      sourceKind: 'channel-message', nativeId: message.external_id },
    readiness: 'read_only',
    freshness: 'imported',
    allowedActions: ['open', 'read'],
    value: message,
  }
}

export function unavailableChannelAdapter(kind: UnavailableChannel['kind']): UnavailableChannel {
  return { kind, readiness: 'unavailable', reason: 'no-typed-assistant-adapter', items: [] }
}
