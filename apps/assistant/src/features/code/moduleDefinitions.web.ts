import type { ModuleDefinition } from '../../shared/shell/webModules.web'
import { resolveCodeRoute } from './codeRoute'

export const codeModuleDefinition: ModuleDefinition = {
  id: 'code',
  mode: 'full',
  matches: route => route.destination === 'apps' && !!route.placement &&
    ['projects', 'code', 'code/workspace', 'projects/detail', 'capabilities/workspace/context'].includes(route.placement.id),
  resolve: resolveCodeRoute,
  load: () => import('./CodeWorkspace.web'),
}
