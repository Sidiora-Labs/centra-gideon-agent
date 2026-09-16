import { useEffect } from 'react'
import { desktopBridge } from './desktopBridge'
import { useChatSocket, type WsMessage } from './useChatSocket'

export const NOTIFICATION_SOURCE_ROUTES: Record<string, string> = {
  agent: 'agents',
  approval: 'chat',
  apps: 'apps',
  cron: 'triggers',
  guardrails: 'notifications',
  heartbeat: 'notifications',
  hook: 'notifications',
  inbox: 'inbox',
  knowledge: 'knowledge',
  learning: 'learning',
  loop: 'loops',
  planning: 'tasks',
  skills: 'skills',
  system: 'notifications',
  user: 'inbox',
}

export const DEFAULT_NOTIFICATION_ROUTE = 'notifications'

export function routeForNote(note: Record<string, unknown>): string {
  const source = typeof note.source === 'string' ? note.source : ''
  return NOTIFICATION_SOURCE_ROUTES[source] || DEFAULT_NOTIFICATION_ROUTE
}

export function shouldDeliverNatively(note: Record<string, unknown>): boolean {
  const native = note.native
  return !!native && typeof native === 'object' && (native as { deliver?: unknown }).deliver === true
}

const KNOWN_ROUTES = new Set(Object.values(NOTIFICATION_SOURCE_ROUTES))

export function useNativeNotifications(navigate: (route: string) => void): void {
  useChatSocket((m: WsMessage) => {
    if (m.type !== 'notification') return
    const bridge = desktopBridge()
    if (!bridge?.notifications) return
    const note = m.data || {}
    if (!shouldDeliverNatively(note)) return
    bridge.notifications
      .show({
        title: String(note.title ?? ''),
        body: String(note.body ?? ''),
        route: routeForNote(note),
      })
      .catch(() => {})
  })

  useEffect(() => {
    const bridge = desktopBridge()
    if (!bridge?.notifications) return
    return bridge.notifications.on((payload) => {
      const route = payload?.route ?? ''
      if (KNOWN_ROUTES.has(route)) navigate(route)
    })
  }, [navigate])
}
