import type { ModuleDefinition } from '../../shared/shell/webModules.web'
import { GatewayError } from '../../shared/transport.web'
import { createCommunicationClient } from './communicationClient'
import { providerItemKey } from './types'

const placementId = 'capabilities/communications/outbound'

function recordIdentity(route: Parameters<ModuleDefinition['matches']>[0], scopeKey: string) {
  if (!route.record) return null
  if (route.record.kind !== 'mail-message' && route.record.kind !== 'mail-draft') return undefined
  let value: unknown
  try { value = JSON.parse(route.record.id) } catch { return undefined }
  if (!Array.isArray(value) || value.length !== 5 || value[0] !== scopeKey || typeof value[1] !== 'string' ||
      value[2] !== 'mail-mirror' || typeof value[4] !== 'string' ||
      value[3] !== (route.record.kind === 'mail-message' ? 'mail-mirror-message' : 'outbound-email-draft')) return undefined
  return { ownerScopeKey: value[0] as string, accountId: value[1] as string, providerKind: value[2] as 'mail-mirror',
    sourceKind: value[3] as 'mail-mirror-message' | 'outbound-email-draft', nativeId: value[4] as string }
}

export const communicationsModuleDefinitions: readonly ModuleDefinition[] = Object.freeze([{
  id: placementId,
  mode: 'full',
  matches: route => route.destination === 'apps' && route.placement?.id === placementId,
  resolve: async (scope, route) => {
    if (scope.runtimeOrigin !== window.location.origin || !scope.ownerId) return 'unavailable'
    const identity = recordIdentity(route, scope.cacheKey)
    if (identity === undefined) return 'unavailable'
    const requestedAccount = route.placement?.query?.account
    if (identity && requestedAccount && identity.accountId !== requestedAccount) return 'denied'
    const accountId = identity?.accountId ?? requestedAccount
    if (!accountId) return route.record ? 'unavailable' : 'available'
    const client = createCommunicationClient(scope)
    try {
      const account = await client.readMirrorAccount(accountId)
      if (!account || account.id !== accountId) return 'missing'
      if (!identity) return 'available'
      client.selectConnection(account.id, 'mail-mirror')
      if (identity.sourceKind === 'mail-mirror-message') {
        const snapshot = await client.readMirrorMessages(account)
        return snapshot.value.some(item => providerItemKey(item.identity) === route.record?.id) ? 'available' : 'missing'
      }
      const drafts = await client.readOutboundDrafts()
      return drafts.some(item => providerItemKey(item.identity) === route.record?.id) ? 'available' : 'missing'
    } catch (error) {
      if (!(error instanceof GatewayError)) return 'unavailable'
      if (error.status === 404) return 'missing'
      if (error.status === 401 || (error.status === 403 && error.authRequired)) return 'denied'
      return 'unavailable'
    } finally { client.dispose() }
  },
  load: () => import('./MailWorkspace.web'),
}])
