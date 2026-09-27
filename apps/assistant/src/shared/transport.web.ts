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
