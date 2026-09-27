export const API_VERSION = '1'
export const SESSION_KEY = 'dashboard:ui'

export type GatewayMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export class GatewayError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code = '',
    readonly retryAfterSeconds?: number,
    readonly authRequired = false,
  ) {
    super(message)
    this.name = 'GatewayError'
  }
}

const expiredListeners = new Set<() => void>()

export function onIdentityExpired(listener: () => void): () => void {
  expiredListeners.add(listener)
  return () => expiredListeners.delete(listener)
}

export function gatewayPath(path: string): string {
  if (!path.startsWith('/api/') || path.startsWith('//') || path.includes('\\')) {
    throw new TypeError('An absolute same-origin API path is required')
  }
  const url = new URL(path, 'https://gideon.invalid')
  if (!url.pathname.startsWith('/api/') || url.hash || url.origin !== 'https://gideon.invalid') {
    throw new TypeError('An absolute same-origin API path is required')
  }
  return `${url.pathname}${url.search}`
}

/** Build a relative URL for a native resource so browser navigation sends its session cookie. */
export function gatewayResourceHref(path: string): string {
  return gatewayPath(path)
}

export function gatewayWebSocketUrl(path: string): string {
  const relative = gatewayPath(path)
  if (typeof window === 'undefined') throw new Error('A browser session is required for a live stream')
  const origin = new URL(window.location.origin)
  const url = new URL(relative, origin)
  if (url.origin !== origin.origin) throw new TypeError('Live streams must use the assistant origin')
  url.protocol = origin.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.href
}

export function openGatewayEventSource(path: string): EventSource {
  if (typeof window === 'undefined' || typeof EventSource === 'undefined') {
    throw new Error('This browser does not support live updates')
  }
  const href = gatewayResourceHref(path)
  const url = new URL(href, window.location.origin)
  if (url.origin !== window.location.origin) throw new TypeError('Live streams must use the assistant origin')
  return new EventSource(url.href, { withCredentials: true })
}

export async function gatewayResource(path: string, signal?: AbortSignal): Promise<Response> {
  const response = await fetch(gatewayResourceHref(path), {
    method: 'GET',
    credentials: 'same-origin',
    cache: 'no-store',
    headers: gatewayHeaders(),
    signal,
  })
  if (!response.ok) {
    await readGatewayJson<unknown>(response)
  }
  return response
}

export function gatewayHeaders(jsonBody = false): Headers {
  const headers = new Headers({
    Accept: 'application/json',
    'X-Gideon-API-Version': API_VERSION,
    'X-Session-Key': SESSION_KEY,
  })
  if (jsonBody) headers.set('Content-Type', 'application/json')
  return headers
}

export function gatewayRequestInit(method: GatewayMethod, body?: unknown, signal?: AbortSignal): RequestInit {
  const hasBody = body !== undefined
  if (hasBody && method === 'GET') throw new TypeError('GET requests cannot have a body')
  return {
    method,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: gatewayHeaders(hasBody),
    body: hasBody ? JSON.stringify(body) : undefined,
    signal,
  }
}

function errorDetail(payload: unknown): { code: string; message: string } {
  if (!payload || typeof payload !== 'object' || !('error' in payload)) return { code: '', message: '' }
  const error = payload.error
  if (typeof error === 'string') return { code: '', message: error }
  if (!error || typeof error !== 'object') return { code: '', message: '' }
  const code = 'code' in error && typeof error.code === 'string' ? error.code : ''
  const message = 'message' in error && typeof error.message === 'string' ? error.message : ''
  return { code, message }
}

export async function readGatewayJson<T>(response: Response, protectedRequest = true): Promise<T> {
  const payload: unknown = await response.json().catch(() => undefined)
  if (!response.ok) {
    if (protectedRequest && (response.status === 401 ||
      (response.status === 403 && response.headers.get('X-Auth-Required') === 'true'))) {
      for (const listener of expiredListeners) listener()
    }
    const { code, message } = errorDetail(payload)
    const retryAfter = Number(response.headers.get('Retry-After'))
    throw new GatewayError(
      message || `Gateway request failed (HTTP ${response.status})`,
      response.status,
      code,
      Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : undefined,
      response.status === 403 && response.headers.get('X-Auth-Required') === 'true',
    )
  }
  if (payload === undefined) throw new GatewayError('Gateway returned an invalid response', response.status)
  return payload as T
}

export async function gatewayJson<T>(
  path: string,
  options: { method?: GatewayMethod; body?: unknown; protectedRequest?: boolean; signal?: AbortSignal } = {},
): Promise<T> {
  const method = options.method ?? 'GET'
  const response = await fetch(gatewayPath(path), gatewayRequestInit(method, options.body, options.signal))
  return readGatewayJson<T>(response, options.protectedRequest !== false)
}
