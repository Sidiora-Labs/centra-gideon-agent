export interface PushPayload { kind: string; item_id: string }
export const PUSH_CUES = ['turn_complete', 'approval_needed', 'error', 'coin_blip', 'terminal_bell'] as const
export type PushCue = typeof PUSH_CUES[number]
export const PUSH_CUE_MESSAGE = 'gideon:play-cue'
export const COMPANION_PATH = '/#/companion'
export interface PushNotification { title: string; body: string; tag: string; url: string; requireInteraction: boolean; sound?: PushCue }
const voices = new Set<string>(PUSH_CUES)
const messages = new Map<string, readonly [string, string]>([
  ['approval', ['Approval needed', 'A run is waiting for your decision.']],
  ['needs_input', ['Loop needs input', 'A loop is waiting on you.']],
  ['inbox_alert', ['Inbox alert', 'Something in your inbox matched an alert.']],
  ['agent_request', ['Agent request', 'Your agent is asking for something.']],
])
const objectValue = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const isVoice = (value: unknown): value is PushCue => typeof value === 'string' && voices.has(value)
export function isPushPayload(value: unknown): value is PushPayload {
  return objectValue(value) && Object.keys(value).length === 2 && Object.hasOwn(value, 'kind') && Object.hasOwn(value, 'item_id') && typeof value.kind === 'string' && value.kind.length > 0 && typeof value.item_id === 'string'
}
export function deepLinkFor(kind: string, itemId: string): string {
  const query = kind === 'approval' && itemId ? `?approval=${encodeURIComponent(itemId)}` : ''
  return COMPANION_PATH + query
}
export function notificationFor(payload: PushPayload, soundByKind?: Readonly<Record<string, string>>): PushNotification {
  const [title, body] = messages.get(payload.kind) ?? ['Gideon', 'Something is waiting for you.']
  const voice = soundByKind?.[payload.kind]
  const notification: PushNotification = { title, body, tag: `gideon:${payload.kind}:${payload.item_id}`, url: deepLinkFor(payload.kind, payload.item_id), requireInteraction: payload.kind === 'approval' }
  if (isVoice(voice)) notification.sound = voice
  return notification
}
export function soundMapFromRules(value: unknown): Record<string, PushCue> {
  const rows = objectValue(value) && Array.isArray(value.rules) ? value.rules : []
  return Object.fromEntries(rows.flatMap((row) => objectValue(row) && typeof row.wire === 'string' && isVoice(row.sound) ? [[row.wire, row.sound]] : []))
}
export function shouldFocus(clientUrl: string, workerOrigin: string): boolean {
  try { return new URL(clientUrl).origin === workerOrigin } catch { return false }
}
