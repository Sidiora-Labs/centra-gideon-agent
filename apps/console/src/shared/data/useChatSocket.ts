import { useEffect, useRef } from 'react'
import { gatewayEvents, type WsMessage } from './socketTransport'

export type { WsMessage } from './socketTransport'

export function useChatSocket(
  onMessage: (message: WsMessage) => void,
  onReconnect?: () => void,
  onStatus?: (connected: boolean) => void,
): void {
  const observer = useRef({ onMessage, onReconnect, onStatus })
  observer.current = { onMessage, onReconnect, onStatus }
  useEffect(() => gatewayEvents().attach({
    message: (message) => observer.current.onMessage(message),
    reconnect: () => observer.current.onReconnect?.(),
    status: (connected) => observer.current.onStatus?.(connected),
  }), [])
}
