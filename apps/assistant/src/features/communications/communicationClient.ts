import { gatewayJson } from '../../shared/transport.web'
import type { OwnerScope } from '../../shared/auth.web'
import {
  connectionReadiness,
  providerItemKey,
  type ApprovalRecord,
  type CalendarDaily,
  type CalendarSource,
  type ChannelSource,
  type CommunicationItem,
  type CommunicationProviderKind,
  type ContentFreshness,
  type InboxRecord,
  type LiveMailboxAvailability,
  type MirrorAccount,
  type MirrorMessage,
  type NotificationRecord,
  type OutboundEmailDraft,
  type PersonRecord,
  type ProviderItemIdentity,
  type ReadSnapshot,
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
  private readonly drafts = new Map<string, OutboundEmailDraft>()

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
    }
  }

  clear(): void {
    this.cache.clear()
    this.drafts.clear()
    this.active = null
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

  async createMirrorAccount(input: Record<string, unknown>, signal?: AbortSignal): Promise<MirrorAccount> {
    const result = await gatewayJson<{ account: MirrorAccount }>(
      '/api/capabilities/communications/mirror/accounts', { method: 'POST', body: input, signal },
    )
    return result.account
  }

  async updateMirrorAccount(accountId: string, input: Record<string, unknown>, signal?: AbortSignal): Promise<MirrorAccount> {
    const result = await gatewayJson<{ account: MirrorAccount }>(
      `/api/capabilities/communications/mirror/accounts/${enc(accountId)}`,
      { method: 'PUT', body: input, signal },
    )
    return result.account
  }

  async readMirrorMessages(account: MirrorAccount, signal?: AbortSignal): Promise<ReadSnapshot<readonly CommunicationItem<MirrorMessage>[]>> {
    this.requireActive(account.id, 'mail-mirror')
    const prior = this.cachedMessages(account.id)
    try {
      const result = await gatewayJson<{ account: MirrorAccount; messages: MirrorMessage[] }>(
        `/api/capabilities/communications/mirror/accounts/${enc(account.id)}/messages`, { signal },
      )
      const imported = account.kind !== 'imap' || result.account.sync?.state === 'imported'
      const items = result.messages.map(message => this.storeItem({
        identity: this.identity(account.id, 'mail-mirror', 'mail-mirror-message', message.external_id),
        readiness: connectionReadiness(result.account.sync?.state),
        freshness: imported ? 'imported' as const : 'current' as const,
        allowedActions: ['open', 'read'],
        value: message,
      }))
      return { value: items, freshness: imported ? 'imported' : 'current' }
    } catch (error) {
      if (!prior.length) throw error
      const stale = prior.map(item => ({ ...item, freshness: 'stale' as const }))
      stale.forEach(item => this.storeItem(item))
      return { value: stale, freshness: 'stale', error: error instanceof Error ? error.message : 'Refresh failed' }
    }
  }

  async uploadMirrorMailbox(accountId: string, content: string, folder: 'INBOX' | 'Sent' = 'INBOX',
    signal?: AbortSignal): Promise<unknown> {
    this.requireActive(accountId, 'mail-mirror')
    return gatewayJson(`/api/capabilities/communications/mirror/accounts/${enc(accountId)}/upload`, {
      method: 'POST', body: { content, folder }, signal,
    })
  }

  async syncMirrorAccount(accountId: string, input: Record<string, unknown> = {}, signal?: AbortSignal): Promise<unknown> {
    this.requireActive(accountId, 'mail-mirror')
    return gatewayJson(`/api/capabilities/communications/mirror/accounts/${enc(accountId)}/sync`, {
      method: 'POST', body: input, signal,
    })
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

  async createCalendarSource(input: Record<string, unknown>, signal?: AbortSignal): Promise<CalendarSource> {
    const result = await gatewayJson<{ source: CalendarSource }>(
      '/api/capabilities/communications/calendar/sources', { method: 'POST', body: input, signal },
    )
    return result.source
  }

  async updateCalendarSource(sourceId: string, input: Record<string, unknown>, signal?: AbortSignal): Promise<CalendarSource> {
    const result = await gatewayJson<{ source: CalendarSource }>(
      `/api/capabilities/communications/calendar/sources/${enc(sourceId)}`, { method: 'PUT', body: input, signal },
    )
    return result.source
  }

  async readCalendarDay(day: string, timezone?: string, signal?: AbortSignal): Promise<CalendarDaily> {
    return gatewayJson(`/api/capabilities/communications/calendar/daily${params({ date: day, timezone })}`, { signal })
  }

  async uploadCalendarSource(sourceId: string, input: Readonly<{
    revision: number
    content: string
    window_start: string
    window_end: string
  }>, signal?: AbortSignal): Promise<unknown> {
    this.requireActive(sourceId, 'calendar-source')
    return gatewayJson(`/api/capabilities/communications/calendar/sources/${enc(sourceId)}/upload`, {
      method: 'POST', body: input, signal,
    })
  }

  async syncCalendarSource(sourceId: string, input: Record<string, unknown>, signal?: AbortSignal): Promise<unknown> {
    this.requireActive(sourceId, 'calendar-source')
    return gatewayJson(`/api/capabilities/communications/calendar/sources/${enc(sourceId)}/sync`, {
      method: 'POST', body: input, signal,
    })
  }

  async readPeople(signal?: AbortSignal): Promise<readonly PersonRecord[]> {
    const result = await gatewayJson<{ people: PersonRecord[] }>(
      '/api/capabilities/communications/people', { signal },
    )
    return result.people
  }

  async readPerson(personId: string, timezone?: string, signal?: AbortSignal): Promise<Readonly<{ person: PersonRecord; touchpoints: readonly unknown[]; care: unknown; timezone: string }>> {
    return gatewayJson(`/api/capabilities/communications/people/${enc(personId)}${params({ timezone })}`, { signal })
  }

  async createPerson(input: Record<string, unknown>, signal?: AbortSignal): Promise<PersonRecord> {
    const result = await gatewayJson<{ person: PersonRecord }>(
      '/api/capabilities/communications/people', { method: 'POST', body: input, signal },
    )
    return result.person
  }

  async updatePerson(personId: string, input: Record<string, unknown>, signal?: AbortSignal): Promise<PersonRecord> {
    const result = await gatewayJson<{ person: PersonRecord }>(
      `/api/capabilities/communications/people/${enc(personId)}`, { method: 'PUT', body: input, signal },
    )
    return result.person
  }

  async recordPersonTouchpoint(personId: string, input: Record<string, unknown>, signal?: AbortSignal): Promise<unknown> {
    return gatewayJson(`/api/capabilities/communications/people/${enc(personId)}/touchpoints`, {
      method: 'POST', body: input, signal,
    })
  }

  async readTeamsSources(signal?: AbortSignal): Promise<readonly ChannelSource[]> {
    const result = await gatewayJson<{ sources: ChannelSource[] }>(
      '/api/capabilities/communications/teams/sources', { signal },
    )
    return result.sources
  }

  async saveTeamsSource(input: Record<string, unknown>, sourceId?: string, signal?: AbortSignal): Promise<ChannelSource> {
    const path = `/api/capabilities/communications/teams/sources${sourceId ? `/${enc(sourceId)}` : ''}`
    const result = await gatewayJson<{ source: ChannelSource }>(path, {
      method: sourceId ? 'PUT' : 'POST', body: input, signal,
    })
    return result.source
  }

  async readTeamsMessages(sourceId: string, signal?: AbortSignal): Promise<Readonly<{ source: ChannelSource; messages: readonly unknown[] }>> {
    return gatewayJson(`/api/capabilities/communications/teams/sources/${enc(sourceId)}/messages`, { signal })
  }

  async syncTeamsSource(sourceId: string, signal?: AbortSignal): Promise<unknown> {
    return gatewayJson(`/api/capabilities/communications/teams/sources/${enc(sourceId)}/sync`, { method: 'POST', body: {}, signal })
  }

  async readSignalArchive(sourceAccountId: string, signal?: AbortSignal): Promise<Readonly<{ imports: readonly unknown[]; history: readonly unknown[] }>> {
    const [imports, history] = await Promise.all([
      gatewayJson<{ imports: unknown[] }>('/api/capabilities/communications/signal-archive/imports', { signal }),
      gatewayJson<{ messages: unknown[] }>(
        `/api/capabilities/communications/signal-archive/history${params({ source_account_id: sourceAccountId })}`,
        { signal },
      ),
    ])
    return { imports: imports.imports, history: history.messages }
  }

  async readOutboundDrafts(signal?: AbortSignal): Promise<readonly OutboundEmailDraft[]> {
    const result = await gatewayJson<{ drafts: OutboundEmailDraft[] }>(
      '/api/capabilities/communications/outbound-email/drafts', { signal },
    )
    for (const draft of result.drafts) this.drafts.set(draft.id, draft)
    return result.drafts
  }

  async createOutboundDraft(input: Readonly<Record<string, unknown> & { account_id: string }>, signal?: AbortSignal): Promise<OutboundEmailDraft> {
    this.requireActive(input.account_id, 'mail-mirror')
    const result = await gatewayJson<{ draft: OutboundEmailDraft }>(
      '/api/capabilities/communications/outbound-email/drafts', { method: 'POST', body: input, signal },
    )
    this.drafts.set(result.draft.id, result.draft)
    return result.draft
  }

  async readOutboundDraft(draftId: string, accountId: string, signal?: AbortSignal): Promise<OutboundEmailDraft> {
    this.requireActive(accountId, 'mail-mirror')
    const result = await gatewayJson<{ draft: OutboundEmailDraft }>(
      `/api/capabilities/communications/outbound-email/drafts/${enc(draftId)}`, { signal },
    )
    if (result.draft.account_id !== accountId) throw new Error('Outbound draft belongs to a different native account')
    this.drafts.set(draftId, result.draft)
    return result.draft
  }

  async approveOutboundDraft(draft: OutboundEmailDraft, signal?: AbortSignal): Promise<OutboundEmailDraft> {
    this.requireActive(draft.account_id, 'mail-mirror')
    const result = await gatewayJson<{ draft: OutboundEmailDraft }>(
      `/api/capabilities/communications/outbound-email/drafts/${enc(draft.id)}/approve`,
      { method: 'POST', body: { revision: draft.revision, content_sha256: draft.content_sha256, confirm_exact: true }, signal },
    )
    if (result.draft.account_id !== draft.account_id) throw new Error('Outbound draft account changed during approval')
    this.drafts.set(draft.id, result.draft)
    return result.draft
  }

  async sendOutboundDraft(draft: OutboundEmailDraft, signal?: AbortSignal): Promise<OutboundEmailDraft> {
    this.requireActive(draft.account_id, 'mail-mirror')
    const result = await gatewayJson<{ draft: OutboundEmailDraft }>(
      `/api/capabilities/communications/outbound-email/drafts/${enc(draft.id)}/send`,
      { method: 'POST', body: { revision: draft.revision, content_sha256: draft.content_sha256, confirm_send: true }, signal },
    )
    if (result.draft.account_id !== draft.account_id) throw new Error('Outbound draft account changed during send')
    this.drafts.set(draft.id, result.draft)
    return result.draft
  }

  async correlateOutboundDraft(draftId: string, accountId: string, signal?: AbortSignal): Promise<OutboundEmailDraft> {
    this.requireActive(accountId, 'mail-mirror')
    const result = await gatewayJson<{ draft: OutboundEmailDraft }>(
      `/api/capabilities/communications/outbound-email/drafts/${enc(draftId)}/correlate`, { method: 'POST', body: {}, signal },
    )
    if (result.draft.account_id !== accountId) throw new Error('Outbound draft belongs to a different native account')
    this.drafts.set(draftId, result.draft)
    return result.draft
  }

  async readInbox(openOnly = false, signal?: AbortSignal): Promise<readonly InboxRecord[]> {
    return gatewayJson(openOnly ? '/api/inbox/open' : '/api/inbox', { signal })
  }

  async markInboxSeen(ids: readonly string[], signal?: AbortSignal): Promise<Readonly<{ ok: boolean; seen: number }>> {
    return gatewayJson('/api/inbox/seen', { method: 'POST', body: { ids }, signal })
  }

  async openInboxItem(id: string, signal?: AbortSignal): Promise<unknown> {
    return gatewayJson(`/api/inbox/${enc(id)}/open`, { method: 'POST', body: {}, signal })
  }

  async updateInboxItem(id: string, input: Record<string, unknown>, signal?: AbortSignal): Promise<unknown> {
    return gatewayJson(`/api/inbox/${enc(id)}`, { method: 'PUT', body: input, signal })
  }

  async saveInboxDraft(id: string, input: Record<string, unknown>, signal?: AbortSignal): Promise<unknown> {
    return gatewayJson(`/api/inbox/${enc(id)}/draft`, { method: 'POST', body: input, signal })
  }

  async applyInboxItem(id: string, input: Record<string, unknown> = {}, signal?: AbortSignal): Promise<unknown> {
    return gatewayJson(`/api/inbox/${enc(id)}/apply`, { method: 'POST', body: input, signal })
  }

  async restoreInboxItem(id: string, signal?: AbortSignal): Promise<unknown> {
    return gatewayJson(`/api/inbox/${enc(id)}/restore`, { method: 'POST', body: {}, signal })
  }

  async readNotifications(signal?: AbortSignal): Promise<Readonly<{ notifications: readonly NotificationRecord[]; unread: number }>> {
    return gatewayJson('/api/notifications', { signal })
  }

  async acknowledgeNotification(ts: string, signal?: AbortSignal): Promise<Readonly<{ ok: boolean }>> {
    return gatewayJson('/api/notifications/ack', { method: 'POST', body: { ts }, signal })
  }

  async restoreNotification(ts: string, signal?: AbortSignal): Promise<Readonly<{ ok: boolean }>> {
    return gatewayJson('/api/notifications/unack', { method: 'POST', body: { ts }, signal })
  }

  async dismissNotification(ts: string, signal?: AbortSignal): Promise<Readonly<{ ok: boolean }>> {
    return gatewayJson('/api/notifications', { method: 'DELETE', body: { ts }, signal })
  }

  async clearNotifications(signal?: AbortSignal): Promise<Readonly<{ ok: boolean }>> {
    return gatewayJson('/api/notifications/clear', { method: 'POST', body: {}, signal })
  }

  async readApprovals(signal?: AbortSignal): Promise<readonly ApprovalRecord[]> {
    return gatewayJson('/api/approvals', { signal })
  }

  async resolveApproval(id: string, action: 'approve' | 'reject', signal?: AbortSignal): Promise<Readonly<{ ok: boolean }>> {
    return gatewayJson(`/api/approvals/${enc(id)}/${action}`, { method: 'POST', body: {}, signal })
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
