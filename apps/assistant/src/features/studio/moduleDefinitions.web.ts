import type { ModuleDefinition } from '../../shared/shell/webModules.web'
import { resolveStudioRoute, studioDestination } from './studioRoutes'

export const studioModules: readonly ModuleDefinition[] = [Object.freeze({
  id: 'studio',
  mode: 'full' as const,
  matches: route => !!studioDestination(route),
  resolve: (scope, route) => resolveStudioRoute(route, scope),
  load: () => import('./StudioWorkspace.web'),
})]
