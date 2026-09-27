import { describe, expect, it } from 'vitest'
import { createCommunicationClient } from './communicationClient'
import { connectionReadiness, providerItemKey, type ProviderItemIdentity } from './types'
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
})
