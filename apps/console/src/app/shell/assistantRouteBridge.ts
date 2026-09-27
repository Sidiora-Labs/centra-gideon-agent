import {
  serializeShellRoute,
  type ShellReturnContext,
  type ShellRoute,
} from '../../../../assistant/src/shared/shell/shellRoutes'
import { parseRouteHash } from './useHashRoute'

export function assistantReturnContextFromConsoleHash(hash: string): ShellReturnContext | undefined {
  const { route, sub } = parseRouteHash(hash, '')
  if (route === 'chat') {
    return sub && sub !== 'new' && sub !== 'history'
      ? { destination: 'chat', sessionId: sub }
      : { destination: 'chat' }
  }
  if (route === 'tasks') {
    return sub ? { destination: 'activity', record: { kind: 'task', id: sub } } : { destination: 'activity' }
  }
  if (route === 'apps') return { destination: 'apps' }
  if (route === 'capabilities' && sub === 'knowledge/ideas') return { destination: 'ideas' }
  if (route === 'capabilities' && sub === 'identity/goals') return { destination: 'goals' }
  return undefined
}

export function assistantHandoffHref(route: ShellRoute, sourceHash = typeof window === 'undefined' ? '' : window.location.hash): string {
  const returnTo = route.returnTo ?? assistantReturnContextFromConsoleHash(sourceHash)
  return serializeShellRoute({ ...route, returnTo })
}

export function navigateToAssistant(route: ShellRoute, sourceHash?: string): void {
  window.location.assign(assistantHandoffHref(route, sourceHash))
}
