import { api } from '../../shared/data/api'
import { pushDeviceId } from './pushClient'

interface CapPushPlugin {
  requestPermissions(): Promise<{ receive: string }>
  register(): Promise<void>
  addListener(eventName: string, callback: (payload: unknown) => void): Promise<unknown> | unknown
}
interface CapBridge { isNativePlatform?: () => boolean; getPlatform?: () => string; Plugins?: Record<string, unknown> }
export interface NativeBridge { platform: 'ios' | 'android'; plugin: CapPushPlugin }
export type NativeEnableResult = { ok: true } | { ok: false; reason: 'no-bridge' | 'denied' | 'error'; detail?: string }

export function nativeBridge(win: unknown = globalThis): NativeBridge | null {
  const bridge = (win as { Capacitor?: CapBridge } | null)?.Capacitor
  if (!bridge?.isNativePlatform?.()) return null
  const platform = bridge.getPlatform?.()
  const plugin = bridge.Plugins?.PushNotifications as CapPushPlugin | undefined
  return plugin && (platform === 'ios' || platform === 'android') ? { platform, plugin } : null
}
function removeListener(handle: unknown) {
  const listener = handle as { remove?: () => unknown } | null
  try { void Promise.resolve(listener?.remove?.()).catch(() => {}) } catch { /* A detached bridge needs no cleanup. */ }
}
async function registrationToken(plugin: CapPushPlugin): Promise<string> {
  const handles: unknown[] = []
  let finished = false
  let timer: ReturnType<typeof setTimeout> | undefined
  const token = new Promise<string>((resolve, reject) => {
    timer = setTimeout(() => reject(new Error('push registration timed out')), 15_000)
    const attach = async (name: string, callback: (data: unknown) => void) => {
      const handle = await plugin.addListener(name, callback)
      if (finished) removeListener(handle)
      else handles.push(handle)
    }
    void Promise.all([
      attach('registration', (data) => resolve(String((data as { value?: unknown } | null)?.value ?? ''))),
      attach('registrationError', (data) => reject(new Error(String((data as { error?: unknown } | null)?.error ?? 'registration failed')))),
    ]).then(() => { if (!finished) return plugin.register() }).catch(reject)
  })
  try { return await token }
  finally { finished = true; clearTimeout(timer); handles.forEach(removeListener) }
}
export async function enableNativePush(win: unknown = globalThis): Promise<NativeEnableResult> {
  const bridge = nativeBridge(win)
  if (!bridge) return { ok: false, reason: 'no-bridge' }
  try {
    if ((await bridge.plugin.requestPermissions()).receive !== 'granted') return { ok: false, reason: 'denied' }
    const token = await registrationToken(bridge.plugin)
    if (!token) return { ok: false, reason: 'error', detail: 'the OS returned an empty token' }
    await api.pushRelayRegister(pushDeviceId(), bridge.platform, token)
    return { ok: true }
  } catch (error) { return { ok: false, reason: 'error', detail: error instanceof Error ? error.message : String(error) } }
}
export async function disableNativePush(): Promise<boolean> {
  return api.pushRelayUnregister(pushDeviceId()).then(() => true, () => false)
}
export function watchNativePushTaps(onItem: (kind: string, itemId: string) => void, win: unknown = globalThis): () => void {
  const bridge = nativeBridge(win)
  let disposed = false
  let handle: unknown
  if (bridge) {
    const attached = bridge.plugin.addListener('pushNotificationActionPerformed', (payload) => {
      if (disposed) return
      const data = (payload as { notification?: { data?: Record<string, unknown> } } | null)?.notification?.data
      const kind = String(data?.kind ?? ''), item = String(data?.item_id ?? '')
      if (kind && item) onItem(kind, item)
    })
    void Promise.resolve(attached).then((listener) => { handle = listener; if (disposed) removeListener(listener) }).catch(() => {})
  }
  return () => { if (!disposed) { disposed = true; removeListener(handle) } }
}
