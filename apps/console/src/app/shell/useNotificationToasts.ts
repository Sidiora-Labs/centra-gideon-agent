import { useChatSocket, type WsMessage } from '../../shared/data/useChatSocket'

export function notificationToast(frame: WsMessage): void {
  if (frame.type !== 'notification') return
  const title = typeof frame.data.title === 'string' ? frame.data.title : ''
  const body = typeof frame.data.body === 'string' ? frame.data.body : ''
  const message = [title, body].filter(Boolean).join(' — ')
  if (!message) return
  const severity = Number(frame.data.severity ?? 1)
  const level = severity >= 3 ? 'error' : severity === 2 ? 'warning' : 'info'
  window.dispatchEvent(new CustomEvent('ne:toast', { detail: { level, message } }))
}

export function useNotificationToasts(): void {
  useChatSocket(notificationToast)
}
