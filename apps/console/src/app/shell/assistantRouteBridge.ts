import {
  serializeShellRoute,
  type ShellPlacement,
  type ShellReturnContext,
  type ShellRoute,
} from '../../../../assistant/src/shared/shell/shellRoutes'

function parseConsoleHash(hash: string): { route: string; sub: string; query: Record<string, string> } {
  const source = hash.replace(/^#?\/?/, '')
  const delimiter = source.indexOf('?')
  const path = delimiter < 0 ? source : source.slice(0, delimiter)
  const segments = path.split('/').filter(Boolean).map(segment => {
    try { return decodeURIComponent(segment) } catch { return segment }
  })
  return {
    route: segments[0] || '', sub: segments.slice(1).join('/'),
    query: Object.fromEntries(new URLSearchParams(delimiter < 0 ? '' : source.slice(delimiter + 1))),
  }
}

const consoleDestinations: Record<ShellReturnContext['destination'], string> = {
  chat: 'chat/new', activity: 'tasks', ideas: 'capabilities/knowledge/ideas',
  goals: 'capabilities/identity/goals', apps: 'apps',
}

function safeQuery(query: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(query).filter(([key, value]) =>
    /^[A-Za-z][A-Za-z0-9_-]{0,39}$/.test(key) &&
    !/secret|token|password|credential|auth|code|key|draft|nonce|access|session|state/i.test(key) &&
    value.length <= 512 && !/[\u0000-\u001f\u007f]/.test(value)).slice(0, 16))
}

export function assistantReturnContextFromConsoleHash(hash: string): ShellReturnContext | undefined {
  const { route, sub, query } = parseConsoleHash(hash)
  const safe = safeQuery(query)
  const placement: ShellPlacement | undefined = Object.keys(safe).length
    ? { id: 'console', query: safe } : undefined
  if (route === 'chat') {
    return sub && sub !== 'new' && sub !== 'history'
      ? { destination: 'chat', sessionId: sub, placement }
      : { destination: 'chat', placement }
  }
  if (route === 'tasks') {
    return sub ? { destination: 'activity', record: { kind: 'task', id: sub }, placement } : { destination: 'activity', placement }
  }
  if (route === 'apps') return { destination: 'apps', placement }
  if (route === 'capabilities' && sub === 'knowledge/ideas') return { destination: 'ideas', placement }
  if (route === 'capabilities' && sub === 'identity/goals') return { destination: 'goals', placement }
  return undefined
}

export function assistantConsoleReturnHref(context: ShellReturnContext): string {
  let path = consoleDestinations[context.destination]
  if (context.destination === 'chat' && context.sessionId) path = `chat/${encodeURIComponent(context.sessionId)}`
  if (context.destination === 'activity' && context.record?.kind === 'task') {
    path = `tasks/${encodeURIComponent(context.record.id)}`
  }
  const query = new URLSearchParams(safeQuery(context.placement?.query ?? {}))
  return `/#/${path}${query.size ? `?${query.toString()}` : ''}`
}

export function assistantHandoffHref(route: ShellRoute, sourceHash = typeof window === 'undefined' ? '' : window.location.hash): string {
  const returnTo = route.returnTo ?? assistantReturnContextFromConsoleHash(sourceHash)
  return serializeShellRoute({ ...route, returnTo })
}

export function navigateToAssistant(route: ShellRoute, sourceHash?: string): void {
  window.location.assign(assistantHandoffHref(route, sourceHash))
}
