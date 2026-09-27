import type { ModuleDefinition } from '../../shared/shell/webModules.web'
import { readActivityDetail, type ActivityDetailKind } from './activityRoutes'

const DETAIL_KINDS: readonly ActivityDetailKind[] = ['trigger_run', 'inbox_item', 'approval', 'notification', 'artifact']

const activityList: ModuleDefinition = {
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
}

const activityDetail: ModuleDefinition = {
  id: 'activity-detail',
  mode: 'full',
  matches: route => route.destination === 'activity' && route.view === 'detail'
    && !!route.record && DETAIL_KINDS.includes(route.record.kind as ActivityDetailKind)
    && (!route.placement || route.placement.id === 'activity'),
  resolve: async (scope, route) => {
    if (route.destination !== 'activity' || route.view !== 'detail' || !route.record
      || !DETAIL_KINDS.includes(route.record.kind as ActivityDetailKind)) return 'unavailable'
    if (!scope.ownerId || scope.runtimeOrigin !== globalThis.location?.origin) return 'denied'
    const result = await readActivityDetail(scope, route.record.kind as ActivityDetailKind, route.record.id)
    return result.state === 'ready' ? 'available' : result.state
  },
  load: () => import('./ActivityDetail.web'),
}

export const activityModuleDefinitions: readonly ModuleDefinition[] = Object.freeze([activityList, activityDetail])
