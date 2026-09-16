/// <reference lib="webworker" />
import { APP_SHELL, SHELL_DOCUMENT, mayCache, strategyFor } from '../shell/swPolicy'
import { COMPANION_PATH, PUSH_CUE_MESSAGE, isPushPayload, notificationFor, shouldFocus, soundMapFromRules, type PushPayload } from '../shell/pushPolicy'

declare const __SW_CACHE_VERSION__: string
const sw = self as unknown as ServiceWorkerGlobalScope
const CACHE_NAME = `gideon-shell-${__SW_CACHE_VERSION__}`

// Existing tabs retain their asset generation: NO `skipWaiting()`, NO `clients.claim()`.
class OfflineShell {
  async install() {
    const cache = await caches.open(CACHE_NAME)
    await cache.addAll([...APP_SHELL])
  }
  async activate() {
    const expired = (await caches.keys()).filter((key) => key !== CACHE_NAME)
    await Promise.all(expired.map((key) => caches.delete(key)))
  }
  async store(request: Request, response: Response) {
    if (response.status !== 200 || !mayCache(new URL(request.url), sw.location.origin)) return
    const cache = await caches.open(CACHE_NAME)
    await cache.put(request, response.clone())
  }
  async navigation(request: Request): Promise<Response> {
    try {
      const response = await fetch(request)
      await this.store(request, response)
      return response
    } catch (error) {
      const cache = await caches.open(CACHE_NAME)
      const fallback = await cache.match(SHELL_DOCUMENT)
      if (fallback) return fallback
      throw error
    }
  }
  async asset(request: Request): Promise<Response> {
    const cache = await caches.open(CACHE_NAME)
    const stored = await cache.match(request)
    if (stored) return stored
    const response = await fetch(request)
    await this.store(request, response)
    return response
  }
  respond(request: Request): Promise<Response> | undefined {
    if (request.method !== 'GET') return
    const strategy = strategyFor(new URL(request.url), sw.location.origin, request.mode === 'navigate')
    const handlers = { 'network-first': () => this.navigation(request), 'cache-first': () => this.asset(request), 'network-only': () => undefined }
    return handlers[strategy]()
  }
}
class PushDelivery {
  async show(payload: PushPayload): Promise<void> {
    const note = notificationFor(payload)
    const options: NotificationOptions & { vibrate: number[] } = {
      body: note.body, tag: note.tag, requireInteraction: note.requireInteraction,
      icon: '/icons/icon-192.png', badge: '/icons/icon-192.png', silent: true,
      vibrate: note.requireInteraction ? [120, 60, 120] : [60], data: { url: note.url },
    }
    await Promise.allSettled([sw.registration.showNotification(note.title, options), this.play(payload)])
  }
  async play(payload: PushPayload): Promise<void> {
    try {
      const response = await fetch('/api/notifications/rules', { credentials: 'same-origin' })
      if (!response.ok) return
      const { sound } = notificationFor(payload, soundMapFromRules(await response.json()))
      if (!sound) return
      const windows = await sw.clients.matchAll({ type: 'window', includeUncontrolled: true })
      windows.forEach((client) => client.postMessage({ type: PUSH_CUE_MESSAGE, cue: sound }))
    } catch { /* Voice delivery must never delay or suppress the visible notification. */ }
  }
  async open(target: string) {
    const windows = await sw.clients.matchAll({ type: 'window', includeUncontrolled: true })
    const existing = windows.find((client) => shouldFocus(client.url, sw.location.origin))
    if (!existing) { await sw.clients.openWindow(target); return }
    try { await existing.navigate(target) } catch { /* An uncontrolled window can still be focused. */ }
    await existing.focus()
  }
}
const shell = new OfflineShell()
const push = new PushDelivery()
sw.addEventListener('install', (event) => event.waitUntil(shell.install()))
sw.addEventListener('activate', (event) => event.waitUntil(shell.activate()))
sw.addEventListener('fetch', (event) => {
  const response = shell.respond(event.request)
  if (response) event.respondWith(response)
})
sw.addEventListener('push', (event) => {
  let payload: unknown
  try { payload = event.data?.json() } catch { payload = undefined }
  event.waitUntil(push.show(isPushPayload(payload) ? payload : { kind: '', item_id: '' }))
})
sw.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = (event.notification.data as { url?: string } | null)?.url || COMPANION_PATH
  event.waitUntil(push.open(target))
})
