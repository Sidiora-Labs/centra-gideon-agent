import { api } from '../../shared/data/api'

const DEVICE_ID_KEY = 'gideon:push:device_id'
let volatileDeviceId: string | undefined
export function pushDeviceId(): string {
  try {
    const saved = localStorage.getItem(DEVICE_ID_KEY)
    if (saved) return saved
  } catch { /* Use the session identity when browser storage is unavailable. */ }
  const id = volatileDeviceId ??= `web-${Math.random().toString(36).slice(2, 10)}`
  try { localStorage.setItem(DEVICE_ID_KEY, id) } catch { /* The session identity remains usable. */ }
  return id
}
export function pushSupported(): boolean {
  const browser = typeof window !== 'undefined' && ['PushManager', 'Notification'].every((key) => key in window)
  return browser && typeof navigator !== 'undefined' && 'serviceWorker' in navigator
}
export function decodeVapidKey(value: string): ArrayBuffer {
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/')
  const bytes = Uint8Array.from(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')), (character) => character.charCodeAt(0))
  return bytes.buffer
}
export type EnableResult = { ok: true; deviceId: string } | { ok: false; reason: 'unsupported' | 'denied' | 'no-key' | 'failed'; detail?: string }
export async function enablePush(vapidPublicKey: string): Promise<EnableResult> {
  const unavailable = !pushSupported() ? 'unsupported' : !vapidPublicKey ? 'no-key' : undefined
  if (unavailable) return { ok: false, reason: unavailable }
  try {
    if (await Notification.requestPermission() !== 'granted') return { ok: false, reason: 'denied' }
    const { pushManager } = await navigator.serviceWorker.ready
    const subscription = await pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: decodeVapidKey(vapidPublicKey) })
    const deviceId = pushDeviceId()
    await api.pushSubscribe(deviceId, subscription.toJSON())
    return { ok: true, deviceId }
  } catch (error) { return { ok: false, reason: 'failed', detail: (error as Error)?.message || '' } }
}
export async function disablePush(): Promise<boolean> {
  try {
    const { pushManager } = await navigator.serviceWorker.ready
    await (await pushManager.getSubscription())?.unsubscribe()
  } catch { /* The gateway must still forget a subscription that cannot be read locally. */ }
  return api.pushUnsubscribe(pushDeviceId()).then(() => true, () => false)
}
