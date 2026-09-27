import type { ModuleDefinition } from '../../shared/shell/webModules.web'
import { readOwnerSession } from '../../shared/auth.web'
import { GatewayError } from '../../shared/transport.web'
import { BrowserClient } from './browserClient'

const isBrowserRoute = (route: Parameters<ModuleDefinition['matches']>[0]) =>
  route.destination === 'apps' && route.view === 'workspace' &&
  route.placement?.id === 'browser/session' && !!route.sessionId && !route.record

export const browserModuleDefinition: ModuleDefinition = {
  id: 'browser/session',
  mode: 'full',
  matches: isBrowserRoute,
  resolve: async (scope, route) => {
    if (!isBrowserRoute(route)) return 'unavailable'
    try {
      const owner = await readOwnerSession()
      if (owner.user !== scope.ownerId) return 'denied'
      const result = await new BrowserClient(scope).open(route.sessionId!)
      if (result.state === 'ready') return 'available'
      if (result.state === 'missing') return 'missing'
      if (result.state === 'signed-out' || result.state === 'denied') return 'denied'
    } catch (error) {
      return error instanceof GatewayError && (error.status === 401 || error.status === 403)
        ? 'denied' : 'unavailable'
    }
    return 'unavailable'
  },
  load: () => import('./BrowserRoute.web'),
}
