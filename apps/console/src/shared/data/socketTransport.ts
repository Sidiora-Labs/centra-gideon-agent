export interface WsMessage { type: string; data: Record<string, unknown> }
export interface SocketObserver {
  message: (message: WsMessage) => void
  reconnect?: () => void
  status?: (connected: boolean) => void
}

export function decodeSocketMessage(raw: unknown): WsMessage | undefined {
  if (typeof raw !== 'string') return undefined
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || !('type' in parsed) || typeof parsed.type !== 'string') return undefined
    return parsed as WsMessage
  } catch { return undefined }
}

export class GatewaySocket {
  private observers = new Map<SocketObserver, boolean>()
  private socket: WebSocket | undefined
  private timer: ReturnType<typeof setTimeout> | undefined
  private failures = 0
  private connected = false

  constructor(private address: string) {}

  attach(observer: SocketObserver): () => void {
    this.observers.set(observer, false)
    if (this.connected) this.announceOpen(observer)
    else if (!this.socket && this.timer === undefined) this.connect()
    return () => {
      this.observers.delete(observer)
      if (!this.observers.size) this.stop()
    }
  }

  private deliver(callback: (() => void) | undefined): void {
    try { callback?.() } catch (error) { console.error('Gateway event subscriber failed', error) }
  }

  private announceOpen(observer: SocketObserver): void {
    const openedBefore = this.observers.get(observer)
    this.observers.set(observer, true)
    this.deliver(() => observer.status?.(true))
    if (openedBefore) this.deliver(observer.reconnect)
  }

  private connect(): void {
    if (!this.observers.size) return
    let socket: WebSocket
    try { socket = new WebSocket(this.address) } catch { this.scheduleReconnect(); return }
    this.socket = socket
    socket.onopen = () => {
      if (this.socket !== socket) return
      this.connected = true
      this.failures = 0
      for (const observer of this.observers.keys()) this.announceOpen(observer)
    }
    socket.onmessage = (event) => {
      if (this.socket !== socket) return
      const message = decodeSocketMessage(event.data)
      if (message) for (const observer of this.observers.keys()) this.deliver(() => observer.message(message))
    }
    socket.onerror = () => { socket.close() }
    socket.onclose = () => {
      if (this.socket !== socket) return
      this.socket = undefined
      this.connected = false
      for (const [observer, opened] of this.observers) {
        if (opened) this.deliver(() => observer.status?.(false))
      }
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect(): void {
    if (!this.observers.size || this.timer !== undefined) return
    this.failures = Math.min(this.failures + 1, 6)
    this.timer = setTimeout(() => {
      this.timer = undefined
      this.connect()
    }, 250 * 2 ** this.failures)
  }

  private stop(): void {
    if (this.timer !== undefined) clearTimeout(this.timer)
    this.timer = undefined
    const socket = this.socket
    this.socket = undefined
    this.connected = false
    this.failures = 0
    if (socket) {
      socket.onopen = socket.onclose = socket.onmessage = socket.onerror = null
      socket.close()
    }
  }
}

let shared: { address: string; transport: GatewaySocket } | undefined
export function gatewayEvents(): GatewaySocket {
  const address = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/ws`
  if (!shared || shared.address !== address) shared = { address, transport: new GatewaySocket(address) }
  return shared.transport
}
