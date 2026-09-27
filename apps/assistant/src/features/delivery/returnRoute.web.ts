import { gatewayJson } from '../../shared/transport.web'
import { serializeShellRoute, type ShellRoute } from '../../shared/shell/shellRoutes'
import {
  assistantConsoleReturnHref,
  stripAssistantOAuthCallback,
  type AssistantOAuthCallback,
} from '../../../../console/src/app/shell/assistantRouteBridge'

export { stripAssistantOAuthCallback }
export type { AssistantOAuthCallback }

const completions = new Map<string, Promise<unknown>>()

export function assistantReturnHref(route: ShellRoute): string {
  if (!route.returnTo) throw new TypeError('A workspace return route is required')
  return assistantConsoleReturnHref(route.returnTo)
}

export function preserveAssistantRoute(route: ShellRoute): string {
  return serializeShellRoute(route)
}

export function completeSpotifyOAuth(callback: AssistantOAuthCallback): Promise<unknown> {
  if (callback.kind !== 'success') return Promise.reject(new Error('Spotify authorization was declined'))
  const prior = completions.get(callback.state)
  if (prior) return prior
  const completion = gatewayJson('/api/capabilities/music/listening/spotify/complete', {
    method: 'POST',
    body: { state: callback.state, code: callback.code },
  })
  completions.set(callback.state, completion)
  return completion
}
