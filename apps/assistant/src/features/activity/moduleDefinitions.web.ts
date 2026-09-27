import type { ModuleDefinition } from '../../shared/shell/webModules.web'

export const activityModuleDefinitions: readonly ModuleDefinition[] = Object.freeze([{
  id: 'activity',
  mode: 'full',
  matches: route => route.destination === 'activity' && route.view === 'list'
    && !route.record && (!route.placement || route.placement.id === 'activity'),
  resolve: async (scope, route) => {
    if (route.destination !== 'activity' || route.view !== 'list' || route.record
      || (route.placement && route.placement.id !== 'activity')) return 'unavailable'
    if (!scope.ownerId || scope.runtimeOrigin !== globalThis.location?.origin) return 'denied'
    return 'available'
  },
  load: () => import('./ActivityScreen'),
}])
