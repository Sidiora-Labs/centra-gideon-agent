import { describe, expect, it } from 'vitest'
import { API_VERSION, GatewayError, gatewayHeaders, gatewayPath, gatewayRequestInit, onIdentityExpired,
  readGatewayJson, SESSION_KEY } from './transport.web'

describe('assistant gateway transport', () => {
  it('keeps API requests on the same origin and carries the gateway contract', () => {
    expect(gatewayPath('/api/auth/session?view=owner')).toBe('/api/auth/session?view=owner')
    for (const path of ['https://elsewhere.test/api/auth/session', '//elsewhere.test/api/auth/session',
      '/api/../../outside', '/api/auth/session#fragment', '/api\\auth\\session']) {
      expect(() => gatewayPath(path)).toThrow(TypeError)
    }
    const headers = gatewayHeaders(true)
    expect(headers.get('X-Session-Key')).toBe(SESSION_KEY)
    expect(headers.get('X-Gideon-API-Version')).toBe(API_VERSION)
    expect(headers.get('Content-Type')).toBe('application/json')
    const request = gatewayRequestInit('POST', { username: 'owner' })
    expect(request.credentials).toBe('same-origin')
    expect(request.cache).toBe('no-store')
    expect(request.body).toBe('{"username":"owner"}')
  })

  it('reads the real error envelope and expires identity only on protected authentication denial', async () => {
    let expirations = 0
    const unsubscribe = onIdentityExpired(() => { expirations += 1 })
    try {
      const denied = new Response(JSON.stringify({ error: { code: 'auth_invalid_credentials', message: 'Denied' } }), {
        status: 401, headers: { 'Content-Type': 'application/json' },
      })
      await expect(readGatewayJson(denied, false)).rejects.toMatchObject({
        status: 401, code: 'auth_invalid_credentials', message: 'Denied',
      } satisfies Partial<GatewayError>)
      expect(expirations).toBe(0)
      const expired = new Response(JSON.stringify({ error: 'Forbidden' }), {
        status: 403, headers: { 'X-Auth-Required': 'true' },
      })
      await expect(readGatewayJson(expired)).rejects.toMatchObject({ status: 403 })
      expect(expirations).toBe(1)
      const forbidden = new Response(JSON.stringify({ error: 'Forbidden' }), { status: 403 })
      await expect(readGatewayJson(forbidden)).rejects.toMatchObject({ status: 403 })
      expect(expirations).toBe(1)
    } finally {
      unsubscribe()
    }
  })

  it('preserves a retry interval and refuses a successful non-JSON body', async () => {
    const limited = new Response(JSON.stringify({ error: { code: 'auth_locked_out' } }), {
      status: 429, headers: { 'Retry-After': '12' },
    })
    await expect(readGatewayJson(limited)).rejects.toMatchObject({
      status: 429, code: 'auth_locked_out', retryAfterSeconds: 12,
    })
    await expect(readGatewayJson(new Response('<html>redirect</html>'))).rejects.toMatchObject({
      message: 'Gateway returned an invalid response',
    })
  })
})
