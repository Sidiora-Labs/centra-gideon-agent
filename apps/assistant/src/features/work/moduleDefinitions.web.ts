import type { ModuleDefinition } from '../../shared/shell/webModules.web'
import type { RouteAvailability } from '../../shared/shell/routeState.web'
import { WORK_DESTINATIONS, findWorkDestination } from './WorkRoutes.web'
import { WorkClient } from './workClient'

const ids = [...new Set(WORK_DESTINATIONS.map(destination => destination.id))]

export const workModuleDefinitions: readonly ModuleDefinition[] = Object.freeze(ids.map(id => ({
  id,
  mode: id.startsWith('rooms') ? 'compact' : 'full',
  matches: route => {
    const page = findWorkDestination(route)
    return page !== null && page.id === id
  },
  resolve: async (scope, route): Promise<RouteAvailability> => {
    const page = findWorkDestination(route)
    if (!page || page.id !== id) return 'unavailable'
    const client = new WorkClient(scope)
    const read = route.record
      ? await client.detail(page.kind, route.record.id)
      : await client.list(page.kind)
    if (read.state === 'denied') return 'denied'
    if (read.state === 'unavailable' || read.state === 'failed') return 'unavailable'
    return 'available'
  },
  load: () => import('./WorkRoutes.web'),
} satisfies ModuleDefinition)))
