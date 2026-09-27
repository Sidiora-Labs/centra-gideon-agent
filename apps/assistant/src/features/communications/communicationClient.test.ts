import { describe, expect, it } from 'vitest'
import { createCommunicationClient } from './communicationClient'
import {
  calendarEventItem,
  connectionReadiness,
  providerItemKey,
  teamsMessageItem,
  type CalendarEvent,
  type CalendarSource,
  type ProviderItemIdentity,
  type TeamsMessage,
  type TeamsSource,
  unavailableChannelAdapter,
} from './types'
import type { OwnerScope } from '../../shared/auth.web'

const scope: OwnerScope = Object.freeze({
  runtimeOrigin: 'http://gideon.local',
  ownerId: 'operator',
  cacheKey: JSON.stringify(['http://gideon.local', 'operator']),
})

function itemIdentity(accountId: string): ProviderItemIdentity {
  return {
    ownerScopeKey: scope.cacheKey,
    accountId,
    providerKind: 'mail-mirror',
    sourceKind: 'mail-mirror-message',
    nativeId: 'same-provider-message-id',
  }
}

describe('communication identity contracts', () => {
  it('keeps equal provider IDs distinct across native accounts for reads and actions', () => {
    const first = itemIdentity('native-account-one')
    const second = itemIdentity('native-account-two')
    expect(providerItemKey(first)).not.toBe(providerItemKey(second))

    const client = createCommunicationClient(scope)
    client.selectConnection(first.accountId, first.providerKind)
    expect(() => client.assertSelectedItem(first)).not.toThrow()
    expect(() => client.assertSelectedItem(second)).toThrow(/Select the source account again/)

    client.selectConnection(second.accountId, second.providerKind)
    expect(() => client.assertSelectedItem(second)).not.toThrow()
    expect(() => client.assertSelectedItem(first)).toThrow(/Select the source account again/)
    client.dispose()
  })

  it('includes owner, provider and source kinds in the composite key', () => {
    const base = itemIdentity('native-account-one')
    expect(providerItemKey({ ...base, ownerScopeKey: 'other-session' })).not.toBe(providerItemKey(base))
    expect(providerItemKey({ ...base, providerKind: 'teams' })).not.toBe(providerItemKey(base))
    expect(providerItemKey({ ...base, sourceKind: 'channel-message' })).not.toBe(providerItemKey(base))
  })

  it('keeps connection states distinct and exposes absent live mailbox reads as empty and unavailable', () => {
    expect(connectionReadiness('configured')).toBe('configured')
    expect(connectionReadiness('connected')).toBe('connected')
    expect(connectionReadiness('read_only')).toBe('read_only')
    expect(connectionReadiness('expired')).toBe('expired')
    expect(connectionReadiness('unavailable')).toBe('unavailable')
    expect(connectionReadiness('importing')).toBe('importing')
    expect(connectionReadiness('failed')).toBe('failed')
    const client = createCommunicationClient(scope)
    expect(client.readLiveMailbox()).toEqual({
      readiness: 'unavailable',
      reason: 'no-registered-live-mailbox-read-route',
      items: [],
    })
    client.dispose()
  })

  it('invalidates captured request tokens on account switch and owner cache clear', () => {
    const client = createCommunicationClient(scope)
    client.selectConnection('native-account-one', 'mail-mirror')
    const first = client.captureSelectionToken()
    expect(client.selectionTokenIsCurrent(first)).toBe(true)
    client.selectConnection('native-account-two', 'mail-mirror')
    expect(client.selectionTokenIsCurrent(first)).toBe(false)
    const second = client.captureSelectionToken()
    client.clear()
    expect(client.selectionTokenIsCurrent(second)).toBe(false)
    client.dispose()
  })

  it('carries source account and native IDs into calendar and Teams item identities', () => {
    const sync = { state: 'synced', coverage: 'available_snapshot', scope: 'provider_window' }
    const calendarSource: CalendarSource = {
      id: 'calendar-native-connection', name: 'Work', kind: 'google', calendar_id: 'primary',
      credential_ref: 'calendar_token', timezone: 'UTC', revision: 4, sync,
      review_coverage: 'available_snapshot',
    }
    const event: CalendarEvent = {
      id: 'same-native-event-id', uid: 'same-native-event-id', title: 'Review', location: '',
      start: '2026-09-27T10:00:00+00:00', end: '2026-09-27T10:30:00+00:00', all_day: false,
      status: 'confirmed', recurrence_unexpanded: false, source_id: calendarSource.id, source_kind: 'google',
    }
    const calendarItem = calendarEventItem(scope.cacheKey, calendarSource, event)
    expect(calendarItem.identity.accountId).toBe(calendarSource.id)
    expect(calendarItem.identity.nativeId).toBe(event.id)

    const teamsSource: TeamsSource = {
      id: 'teams-native-connection', name: 'Company', owner_email: 'owner@example.com',
      credential_ref: 'graph_token', revision: 2, sync,
    }
    const message: TeamsMessage = {
      provenance_key: 'channel:thread:same-native-event-id', provider: 'microsoft_graph',
      source_kind: 'channel', conversation_id: 'thread', team_id: 'team', channel_id: 'channel',
      message_id: 'same-native-event-id', reply_to_id: null, sender: { id: 'user', name: 'User' },
      person_id: null, direction: 'inbound', created_at: '2026-09-27T10:00:00+00:00',
      modified_at: null, deleted_at: null, etag: '', body: 'Message', attachments: [],
    }
    const teamsItem = teamsMessageItem(scope.cacheKey, teamsSource, message)
    expect(teamsItem.identity.accountId).toBe(teamsSource.id)
    expect(teamsItem.identity.nativeId).toBe(message.provenance_key)
    expect(providerItemKey(calendarItem.identity)).not.toBe(providerItemKey(teamsItem.identity))
    expect(unavailableChannelAdapter('beeper')).toEqual({
      kind: 'beeper', readiness: 'unavailable', reason: 'no-typed-assistant-adapter', items: [],
    })
  })
})
