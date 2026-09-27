import { GatewayError, gatewayJson } from '../../shared/transport.web'
import type { OwnerScope } from '../../shared/auth.web'
import {
  mirrorReadiness,
  providerItemKey,
  type ApprovalRecord,
  type CalendarDaily,
  type CalendarEvent,
  type CalendarSource,
  calendarEventItem,
  type CommunicationItem,
  type CommunicationProviderKind,
  type InboxRecord,
  type InboxUpdate,
  type InboxActionResult,
  type InboxApplyResult,
  type LiveMailboxAvailability,
  type MirrorAccount,
  type MirrorAccountInput,
  type MirrorMessage,
  type MirrorMessageReadState,
  type OutboundDraftEditInput,
  type MirrorSyncResult,
  type MirrorUploadResult,
  type MirrorUploadInput,
  type NotificationRecord,
  type OutboundDraftInput,
  type OutboundDraftItem,
  type OutboundEmailDraft,
  type NotificationActionResult,
  type PersonDetail,
  type PersonInput,
  type PersonRecord,
  type PersonTouchpoint,
  type ProviderItemIdentity,
  type ReadSnapshot,
  type CalendarSourceInput,
  type CalendarUploadInput,
  type CalendarSyncInput,
  type SyncState,
  type SelectionToken,
  type TeamsMessage,
  type TeamsSource,
  type TeamsSourceInput,
  type TeamsSyncResult,
  type SignalArchiveMessage,
  type SignalArchiveReceipt,
  type TouchpointInput,
  teamsMessageItem,
  signalArchiveMessageItem,
  type JsonValue,
} from './types'

type ActiveConnection = Readonly<{ accountId: string; providerKind: CommunicationProviderKind }>
type CacheRow = CommunicationItem<unknown>
const clientsByOwner = new Map<string, Set<CommunicationClient>>()

const enc = (value: string) => encodeURIComponent(value)
const params = (values: Record<string, string | number | undefined>) => {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) if (value !== undefined) query.set(key, String(value))
  const result = query.toString()
  return result ? `?${result}` : ''
}

export class CommunicationClient {
  private active: ActiveConnection | null = null
  private readonly cache = new Map<string, CacheRow>()
  private readonly drafts = new Map<string, OutboundDraftItem>()
  private generation = 0

  constructor(readonly scope: OwnerScope) {
    const clients = clientsByOwner.get(scope.cacheKey) ?? new Set<CommunicationClient>()
    clients.add(this)
    clientsByOwner.set(scope.cacheKey, clients)
  }

  selectConnection(accountId: string, providerKind: CommunicationProviderKind): void {
    if (!accountId) throw new TypeError('A native connection account ID is required')
    if (this.active?.accountId !== accountId || this.active.providerKind !== providerKind) {
      this.cache.clear()
      this.drafts.clear()
      this.active = Object.freeze({ accountId, providerKind })
      this.generation += 1
    }
  }

  clear(): void {
    this.cache.clear()
    this.drafts.clear()
    this.active = null
    this.generation += 1
  }

  dispose(): void {
    this.clear()
    const clients = clientsByOwner.get(this.scope.cacheKey)
    clients?.delete(this)
    if (clients?.size === 0) clientsByOwner.delete(this.scope.cacheKey)
  }

  private identity(accountId: string, providerKind: CommunicationProviderKind,
    sourceKind: ProviderItemIdentity['sourceKind'], nativeId: string): ProviderItemIdentity {
    return { ownerScopeKey: this.scope.cacheKey, accountId, providerKind, sourceKind, nativeId }
  }

  private cachedItem<T>(identity: ProviderItemIdentity): CommunicationItem<T> | undefined {
    return this.cache.get(providerItemKey(identity)) as CommunicationItem<T> | undefined
  }

  private storeItem<T>(item: CommunicationItem<T>): CommunicationItem<T> {
    this.cache.set(providerItemKey(item.identity), item as CacheRow)
    return item
  }

  private requireActive(accountId: string, providerKind?: CommunicationProviderKind): void {
    if (!this.active || this.active.accountId !== accountId ||
      (providerKind !== undefined && this.active.providerKind !== providerKind)) {
      throw new Error('Select the source account again before using this communication item')
    }
  }

  captureSelectionToken(): SelectionToken {
    if (!this.active) throw new Error('Select a source account before loading its records')
    return Object.freeze({ generation: this.generation, ...this.active })
  }

  selectionTokenIsCurrent(token: SelectionToken): boolean {
    return token.generation === this.generation && this.active?.accountId === token.accountId &&
      this.active.providerKind === token.providerKind
  }

  private requireCurrentSelection(token: SelectionToken): void {
    if (!this.selectionTokenIsCurrent(token)) {
      throw new Error('Source selection changed during the request; refresh the selected account to confirm its result')
    }
  }

  private async selectedRequest<T>(token: SelectionToken, request: Promise<T>): Promise<T> {
    try {
      const value = await request
      this.requireCurrentSelection(token)
      return value
    } catch (error) {
      this.requireCurrentSelection(token)
      throw error
    }
  }

  private cacheDraft(draft: OutboundEmailDraft): OutboundDraftItem {
    const item: OutboundDraftItem = {
      identity: this.identity(draft.account_id, 'mail-mirror', 'outbound-email-draft', draft.id),
      readiness: 'configured',
      freshness: 'current',
      allowedActions: draft.state === 'draft' ? ['edit', 'review'] :
        draft.state === 'approved' ? ['send'] : ['read', 'reconcile'],
      value: draft,
    }
    this.drafts.set(providerItemKey(item.identity), item)
    this.storeItem(item)
    return item
  }

  async readMirrorAccounts(signal?: AbortSignal): Promise<readonly MirrorAccount[]> {
    const result = await gatewayJson<{ accounts: MirrorAccount[] }>(
      '/api/capabilities/communications/mirror/accounts', { signal },
    )
    return result.accounts
  }

  async readMirrorAccount(accountId: string, signal?: AbortSignal): Promise<MirrorAccount> {
    const result = await gatewayJson<{ account: MirrorAccount }>(
      `/api/capabilities/communications/mirror/accounts/${enc(accountId)}`, { signal },
    )
    return result.account
  }

  async createMirrorAccount(input: MirrorAccountInput, signal?: AbortSignal): Promise<MirrorAccount> {
    const result = await gatewayJson<{ account: MirrorAccount }>(
      '/api/capabilities/communications/mirror/accounts', { method: 'POST', body: input, signal },
    )
    return result.account
  }

  async updateMirrorAccount(accountId: string, input: MirrorAccountInput & Readonly<{ revision: number }>, signal?: AbortSignal): Promise<MirrorAccount> {
    const result = await gatewayJson<{ account: MirrorAccount }>(
      `/api/capabilities/communications/mirror/accounts/${enc(accountId)}`,
      { method: 'PUT', body: input, signal },
    )
    return result.account
  }

  async readMirrorMessages(account: MirrorAccount, signal?: AbortSignal): Promise<ReadSnapshot<readonly CommunicationItem<MirrorMessage>[]>> {
    this.requireActive(account.id, 'mail-mirror')
    const token = this.captureSelectionToken()
    const prior = this.cachedMessages(account.id)
    try {
      const result = await this.selectedRequest(token, gatewayJson<{ account: MirrorAccount; messages: MirrorMessage[] }>(
        `/api/capabilities/communications/mirror/accounts/${enc(account.id)}/messages`, { signal },
      ))
      if (result.account.id !== account.id) throw new Error('Mailbox response belongs to a different native account')
      const imported = account.kind !== 'imap' || result.account.sync?.state === 'imported'
      const items = result.messages.map(message => this.storeItem({
        identity: this.identity(account.id, 'mail-mirror', 'mail-mirror-message', message.external_id),
        readiness: mirrorReadiness(result.account),
        freshness: imported ? 'imported' as const : 'current' as const,
        allowedActions: ['open', 'read'],
        value: message,
      }))
      return { value: items, freshness: imported ? 'imported' : 'current' }
    } catch (error) {
      this.requireCurrentSelection(token)
      if (error instanceof GatewayError && (error.status === 401 || error.status === 403)) {
        prior.forEach(item => this.cache.delete(providerItemKey(item.identity)))
        throw error
      }
      if (!prior.length) throw error
      const stale = prior.map(item => ({ ...item, freshness: 'stale' as const }))
      stale.forEach(item => this.storeItem(item))
      return { value: stale, freshness: 'stale', error: error instanceof Error ? error.message : 'Refresh failed' }
    }
  }

  async setMirrorMessageReadState(accountId: string, externalId: string, isRead: boolean,
    signal?: AbortSignal): Promise<CommunicationItem<MirrorMessage> | undefined> {
    this.requireActive(accountId, 'mail-mirror')
    const token = this.captureSelectionToken()
    const result = await this.selectedRequest(token, gatewayJson<{ message: MirrorMessageReadState }>(
      `/api/capabilities/communications/mirror/accounts/${enc(accountId)}/messages/${enc(externalId)}/read-state`,
      { method: 'PATCH', body: { is_read: isRead }, signal },
    ))
    if (result.message.account_id !== accountId || result.message.external_id !== externalId) {
      throw new Error('Read-state response belongs to a different native mailbox item')
    }
    const identity: ProviderItemIdentity = this.identity(accountId, 'mail-mirror', 'mail-mirror-message', externalId)
    const prior = this.cachedItem<MirrorMessage>(identity)
    return prior ? this.storeItem({ ...prior, value: { ...prior.value, is_read: result.message.is_read, read_state: result.message.read_state } }) : undefined
  }

  async uploadMirrorMailbox(accountId: string, input: MirrorUploadInput,
    signal?: AbortSignal): Promise<MirrorUploadResult> {
    this.requireActive(accountId, 'mail-mirror')
    return gatewayJson<MirrorUploadResult>(`/api/capabilities/communications/mirror/accounts/${enc(accountId)}/upload`, {
      method: 'POST', body: input, signal,
    })
  }

  async syncMirrorAccount(accountId: string, input: Readonly<Record<string, never>> = {}, signal?: AbortSignal): Promise<MirrorSyncResult> {
    this.requireActive(accountId, 'mail-mirror')
    const result = await gatewayJson<{ sync: MirrorSyncResult }>(`/api/capabilities/communications/mirror/accounts/${enc(accountId)}/sync`, {
      method: 'POST', body: input, signal,
    })
    return result.sync
  }

  readLiveMailbox(): LiveMailboxAvailability {
    return { readiness: 'unavailable', reason: 'no-registered-live-mailbox-read-route', items: [] }
  }

  async readCalendarSources(signal?: AbortSignal): Promise<readonly CalendarSource[]> {
    const result = await gatewayJson<{ sources: CalendarSource[] }>(
      '/api/capabilities/communications/calendar/sources', { signal },
    )
    return result.sources
  }

  async createCalendarSource(input: CalendarSourceInput, signal?: AbortSignal): Promise<CalendarSource> {
    const result = await gatewayJson<{ source: CalendarSource }>(
      '/api/capabilities/communications/calendar/sources', { method: 'POST', body: input, signal },
    )
    return result.source
  }

  async updateCalendarSource(sourceId: string, input: CalendarSourceInput & Readonly<{ revision: number }>, signal?: AbortSignal): Promise<CalendarSource> {
    const result = await gatewayJson<{ source: CalendarSource }>(
      `/api/capabilities/communications/calendar/sources/${enc(sourceId)}`, { method: 'PUT', body: input, signal },
    )
    return result.source
  }

  async readCalendarDay(day: string, timezone?: string, signal?: AbortSignal): Promise<CalendarDaily> {
    const result = await gatewayJson<Omit<CalendarDaily, 'events'> & { events: CalendarEvent[] }>(
      `/api/capabilities/communications/calendar/daily${params({ date: day, timezone })}`, { signal },
    )
    const sources = new Map(result.sources.map(source => [source.id, source]))
    return { ...result, events: result.events.map(event => {
      const source = sources.get(event.source_id)
      if (!source) throw new Error(`Calendar event ${event.id} has no registered source`)
      return calendarEventItem(this.scope.cacheKey, source, event)
    }) }
  }

  async uploadCalendarSource(sourceId: string, input: CalendarUploadInput, signal?: AbortSignal): Promise<SyncState> {
    this.requireActive(sourceId, 'calendar-source')
    const result = await gatewayJson<{ sync: SyncState }>(`/api/capabilities/communications/calendar/sources/${enc(sourceId)}/upload`, {
      method: 'POST', body: input, signal,
    })
    return result.sync
  }

  async syncCalendarSource(sourceId: string, input: CalendarSyncInput, signal?: AbortSignal): Promise<SyncState> {
    this.requireActive(sourceId, 'calendar-source')
    const result = await gatewayJson<{ sync: SyncState }>(`/api/capabilities/communications/calendar/sources/${enc(sourceId)}/sync`, {
      method: 'POST', body: input, signal,
    })
    return result.sync
  }

  async readPeople(signal?: AbortSignal): Promise<readonly PersonRecord[]> {
    const result = await gatewayJson<{ people: PersonRecord[] }>(
      '/api/capabilities/communications/people', { signal },
    )
    return result.people
  }

  async readPerson(personId: string, timezone?: string, signal?: AbortSignal): Promise<PersonDetail> {
    return gatewayJson<PersonDetail>(`/api/capabilities/communications/people/${enc(personId)}${params({ timezone })}`, { signal })
  }

  async createPerson(input: PersonInput, signal?: AbortSignal): Promise<PersonRecord> {
    const result = await gatewayJson<{ person: PersonRecord }>(
      '/api/capabilities/communications/people', { method: 'POST', body: input, signal },
    )
    return result.person
  }

  async updatePerson(personId: string, input: PersonInput & Readonly<{ revision: number }>, signal?: AbortSignal): Promise<PersonRecord> {
    const result = await gatewayJson<{ person: PersonRecord }>(
      `/api/capabilities/communications/people/${enc(personId)}`, { method: 'PUT', body: input, signal },
    )
    return result.person
  }

  async recordPersonTouchpoint(personId: string, input: TouchpointInput, signal?: AbortSignal): Promise<Readonly<{ touchpoint: PersonTouchpoint; created: boolean }>> {
    return gatewayJson(`/api/capabilities/communications/people/${enc(personId)}/touchpoints`, {
      method: 'POST', body: input, signal,
    })
  }

  async readTeamsSources(signal?: AbortSignal): Promise<readonly TeamsSource[]> {
    const result = await gatewayJson<{ sources: TeamsSource[] }>(
      '/api/capabilities/communications/teams/sources', { signal },
    )
    return result.sources
  }

  async saveTeamsSource(input: TeamsSourceInput, sourceId?: undefined, signal?: AbortSignal): Promise<TeamsSource>
  async saveTeamsSource(input: TeamsSourceInput & Readonly<{ revision: number }>, sourceId: string, signal?: AbortSignal): Promise<TeamsSource>
  async saveTeamsSource(input: TeamsSourceInput & Readonly<{ revision?: number }>, sourceId?: string, signal?: AbortSignal): Promise<TeamsSource> {
    const path = `/api/capabilities/communications/teams/sources${sourceId ? `/${enc(sourceId)}` : ''}`
    const result = await gatewayJson<{ source: TeamsSource }>(path, {
      method: sourceId ? 'PUT' : 'POST', body: input, signal,
    })
    return result.source
  }

  async readTeamsMessages(sourceId: string, signal?: AbortSignal): Promise<Readonly<{ source: TeamsSource; messages: readonly CommunicationItem<TeamsMessage>[] }>> {
    this.requireActive(sourceId, 'teams')
    const token = this.captureSelectionToken()
    const result = await this.selectedRequest(token, gatewayJson<{ source: TeamsSource; messages: TeamsMessage[] }>(
      `/api/capabilities/communications/teams/sources/${enc(sourceId)}/messages`, { signal },
    ))
    if (result.source.id !== sourceId) throw new Error('Channel response belongs to a different native source')
    return { source: result.source, messages: result.messages.map(message =>
      teamsMessageItem(this.scope.cacheKey, result.source, message)) }
  }

  async syncTeamsSource(sourceId: string, signal?: AbortSignal): Promise<TeamsSyncResult> {
    this.requireActive(sourceId, 'teams')
    const token = this.captureSelectionToken()
    return this.selectedRequest(token, gatewayJson<{ sync: TeamsSyncResult }>(`/api/capabilities/communications/teams/sources/${enc(sourceId)}/sync`, { method: 'POST', body: {}, signal })).then(result => result.sync)
  }

  async readSignalArchive(sourceAccountId: string, signal?: AbortSignal): Promise<Readonly<{
    imports: readonly SignalArchiveReceipt[]
    history: readonly CommunicationItem<SignalArchiveMessage>[]
  }>> {
    const [imports, history] = await Promise.all([
      gatewayJson<{ imports: SignalArchiveReceipt[] }>('/api/capabilities/communications/signal-archive/imports', { signal }),
      gatewayJson<{ messages: SignalArchiveMessage[] }>(
        `/api/capabilities/communications/signal-archive/history${params({ source_account_id: sourceAccountId })}`,
        { signal },
      ),
    ])
    return {
      imports: imports.imports.filter(receipt => receipt.source_account_id === sourceAccountId),
      history: history.messages.map(message => signalArchiveMessageItem(this.scope.cacheKey, sourceAccountId, message)),
    }
  }

  async readOutboundDrafts(signal?: AbortSignal): Promise<readonly OutboundDraftItem[]> {
    const token = this.captureSelectionToken()
    this.requireActive(token.accountId, 'mail-mirror')
    const result = await this.selectedRequest(token, gatewayJson<{ drafts: OutboundEmailDraft[] }>(
      '/api/capabilities/communications/outbound-email/drafts', { signal },
    ))
    return result.drafts.filter(draft => draft.account_id === token.accountId).map(draft => this.cacheDraft(draft))
  }

  async createOutboundDraft(input: OutboundDraftInput, signal?: AbortSignal): Promise<OutboundDraftItem> {
    this.requireActive(input.account_id, 'mail-mirror')
    const token = this.captureSelectionToken()
    const result = await this.selectedRequest(token, gatewayJson<{ draft: OutboundEmailDraft }>(
      '/api/capabilities/communications/outbound-email/drafts', { method: 'POST', body: input, signal },
    ))
    if (result.draft.account_id !== input.account_id) throw new Error('Outbound draft belongs to a different native account')
    return this.cacheDraft(result.draft)
  }

  async readOutboundDraft(draftId: string, accountId: string, signal?: AbortSignal): Promise<OutboundDraftItem> {
    this.requireActive(accountId, 'mail-mirror')
    const token = this.captureSelectionToken()
    const result = await this.selectedRequest(token, gatewayJson<{ draft: OutboundEmailDraft }>(
      `/api/capabilities/communications/outbound-email/drafts/${enc(draftId)}`, { signal },
    ))
    if (result.draft.account_id !== accountId) throw new Error('Outbound draft belongs to a different native account')
    return this.cacheDraft(result.draft)
  }

  async editOutboundDraft(item: OutboundDraftItem, input: OutboundDraftEditInput,
    signal?: AbortSignal): Promise<OutboundDraftItem> {
    this.assertSelectedItem(item.identity)
    if (input.account_id !== item.identity.accountId || input.revision !== item.value.revision) {
      throw new Error('Outbound draft account or revision changed; reload before editing')
    }
    const token = this.captureSelectionToken()
    const result = await this.selectedRequest(token, gatewayJson<{ draft: OutboundEmailDraft }>(
      `/api/capabilities/communications/outbound-email/drafts/${enc(item.value.id)}`,
      { method: 'PATCH', body: input, signal },
    ))
    if (result.draft.id !== item.value.id || result.draft.account_id !== item.identity.accountId) {
      throw new Error('Edited draft identity changed during the update')
    }
    return this.cacheDraft(result.draft)
  }

  async approveOutboundDraft(item: OutboundDraftItem, signal?: AbortSignal): Promise<OutboundDraftItem> {
    this.assertSelectedItem(item.identity)
    const token = this.captureSelectionToken()
    const draft = item.value
    const result = await this.selectedRequest(token, gatewayJson<{ draft: OutboundEmailDraft }>(
      `/api/capabilities/communications/outbound-email/drafts/${enc(draft.id)}/approve`,
      { method: 'POST', body: { revision: draft.revision, content_sha256: draft.content_sha256, confirm_exact: true }, signal },
    ))
    if (result.draft.account_id !== draft.account_id) throw new Error('Outbound draft account changed during approval')
    return this.cacheDraft(result.draft)
  }

  async sendOutboundDraft(item: OutboundDraftItem, signal?: AbortSignal): Promise<OutboundDraftItem> {
    this.assertSelectedItem(item.identity)
    const token = this.captureSelectionToken()
    const draft = item.value
    const result = await this.selectedRequest(token, gatewayJson<{ draft: OutboundEmailDraft }>(
      `/api/capabilities/communications/outbound-email/drafts/${enc(draft.id)}/send`,
      { method: 'POST', body: { revision: draft.revision, content_sha256: draft.content_sha256, confirm_send: true }, signal },
    ))
    if (result.draft.account_id !== draft.account_id) throw new Error('Outbound draft account changed during send')
    return this.cacheDraft(result.draft)
  }

  async correlateOutboundDraft(draftId: string, accountId: string, signal?: AbortSignal): Promise<OutboundDraftItem> {
    this.requireActive(accountId, 'mail-mirror')
    const token = this.captureSelectionToken()
    const result = await this.selectedRequest(token, gatewayJson<{ draft: OutboundEmailDraft }>(
      `/api/capabilities/communications/outbound-email/drafts/${enc(draftId)}/correlate`, { method: 'POST', body: {}, signal },
    ))
    if (result.draft.account_id !== accountId) throw new Error('Outbound draft belongs to a different native account')
    return this.cacheDraft(result.draft)
  }

  async readInbox(openOnly = false, signal?: AbortSignal): Promise<readonly InboxRecord[]> {
    return gatewayJson<InboxRecord[]>(openOnly ? '/api/inbox/open' : '/api/inbox', { signal })
  }

  async markInboxSeen(ids: readonly string[], signal?: AbortSignal): Promise<Readonly<{ ok: boolean; seen: number }>> {
    return gatewayJson<{ ok: boolean; seen: number }>('/api/inbox/seen', { method: 'POST', body: { ids }, signal })
  }

  async openInboxItem(id: string, signal?: AbortSignal): Promise<InboxActionResult> {
    return gatewayJson<InboxActionResult>(`/api/inbox/${enc(id)}/open`, { method: 'POST', body: {}, signal })
  }

  async updateInboxItem(id: string, input: InboxUpdate, signal?: AbortSignal): Promise<InboxRecord> {
    return gatewayJson<InboxRecord>(`/api/inbox/${enc(id)}`, { method: 'PUT', body: input, signal })
  }

  async saveInboxDraft(id: string, signal?: AbortSignal): Promise<InboxRecord> {
    return gatewayJson<InboxRecord>(`/api/inbox/${enc(id)}/draft`, { method: 'POST', body: {}, signal })
  }

  async applyInboxItem(id: string, proposal?: Readonly<Record<string, JsonValue>>, signal?: AbortSignal): Promise<InboxApplyResult> {
    return gatewayJson<InboxApplyResult>(`/api/inbox/${enc(id)}/apply`, { method: 'POST', body: proposal ? { proposal } : {}, signal })
  }

  async restoreInboxItem(id: string, signal?: AbortSignal): Promise<InboxRecord> {
    return gatewayJson<InboxRecord>(`/api/inbox/${enc(id)}/restore`, { method: 'POST', body: {}, signal })
  }

  async readNotifications(signal?: AbortSignal): Promise<Readonly<{ notifications: readonly NotificationRecord[]; unread: number }>> {
    return gatewayJson<Readonly<{ notifications: NotificationRecord[]; unread: number }>>('/api/notifications', { signal })
  }

  async acknowledgeNotification(ts: string, signal?: AbortSignal): Promise<NotificationActionResult> {
    return gatewayJson<NotificationActionResult>('/api/notifications/ack', { method: 'POST', body: { ts }, signal })
  }

  async restoreNotification(ts: string, signal?: AbortSignal): Promise<NotificationActionResult> {
    return gatewayJson<NotificationActionResult>('/api/notifications/unack', { method: 'POST', body: { ts }, signal })
  }

  async dismissNotification(ts: string, signal?: AbortSignal): Promise<NotificationActionResult> {
    return gatewayJson<NotificationActionResult>('/api/notifications', { method: 'DELETE', body: { ts }, signal })
  }

  async clearNotifications(signal?: AbortSignal): Promise<NotificationActionResult> {
    return gatewayJson<NotificationActionResult>('/api/notifications/clear', { method: 'POST', body: {}, signal })
  }

  async readApprovals(signal?: AbortSignal): Promise<readonly ApprovalRecord[]> {
    return gatewayJson<ApprovalRecord[]>('/api/approvals', { signal })
  }

  async resolveApproval(id: string, action: 'approve' | 'reject', signal?: AbortSignal): Promise<InboxActionResult> {
    return gatewayJson<InboxActionResult>(`/api/approvals/${enc(id)}/${action}`, { method: 'POST', body: {}, signal })
  }

  assertSelectedItem(identity: ProviderItemIdentity): void {
    if (identity.ownerScopeKey !== this.scope.cacheKey) {
      throw new Error('Communication item belongs to a different owner session')
    }
    this.requireActive(identity.accountId, identity.providerKind)
  }

  private cachedMessages(accountId: string): readonly CommunicationItem<MirrorMessage>[] {
    const prefix = JSON.stringify([this.scope.cacheKey, accountId, 'mail-mirror', 'mail-mirror-message']).slice(0, -1)
    return [...this.cache.entries()]
      .filter(([key]) => key.startsWith(prefix))
      .map(([, row]) => row as CommunicationItem<MirrorMessage>)
  }
}

export function createCommunicationClient(scope: OwnerScope): CommunicationClient {
  return new CommunicationClient(scope)
}

export function clearOwnerCommunicationCache(scope: OwnerScope): void {
  const clients = clientsByOwner.get(scope.cacheKey)
  if (!clients) return
  for (const client of clients) client.clear()
  clientsByOwner.delete(scope.cacheKey)
}
