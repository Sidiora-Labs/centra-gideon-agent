import { describe, expect, it } from 'vitest'
import { identityErrorMessage, ownerScope, signInControls } from './auth.web'
import { isIdentityDenied } from './bootstrap.web'
import { GatewayError } from './transport.web'

describe('assistant owner bootstrap', () => {
  it('offers password and authenticator controls when configured or required by login', () => {
    expect(signInControls({ login_enabled: true, totp_required: true }))
      .toEqual({ password: true, totp: true })
    expect(signInControls({ login_enabled: true, totp_required: false }))
      .toEqual({ password: true, totp: false })
    expect(signInControls({ login_enabled: true, totp_required: false }, true))
      .toEqual({ password: true, totp: true })
  })

  it('offers retry without a password form when password login is disabled', () => {
    expect(signInControls({ login_enabled: false, totp_required: false }))
      .toEqual({ password: false, totp: false })
  })

  it('distinguishes expired identity from denied operations and explains sign-in errors', () => {
    expect(isIdentityDenied(new GatewayError('Unauthorized', 401))).toBe(true)
    expect(isIdentityDenied(new GatewayError('Forbidden', 403))).toBe(true)
    expect(isIdentityDenied(new GatewayError('Wrong origin', 403, 'auth_origin_not_allowed'))).toBe(false)
    expect(isIdentityDenied(new GatewayError('Failed', 500))).toBe(false)
    expect(identityErrorMessage(new GatewayError('code needed', 401, 'auth_totp_required')))
      .toContain('authenticator app')
    expect(identityErrorMessage(new GatewayError('locked', 429, 'auth_locked_out', 12)))
      .toContain('12 seconds')
  })

  it('scopes owner data to the runtime origin and authenticated server identity', () => {
    const first = ownerScope('https://gideon.example:9443/path', { user: 'owner-a' })
    expect(first).toEqual(ownerScope('https://gideon.example:9443/other', { user: 'owner-a' }))
    expect(first.runtimeOrigin).toBe('https://gideon.example:9443')
    expect(first.cacheKey).not.toBe(ownerScope('https://gideon.example:9443', { user: 'owner-b' }).cacheKey)
    expect(first.cacheKey).not.toBe(ownerScope('https://other.example:9443', { user: 'owner-a' }).cacheKey)
    expect(() => ownerScope('https://gideon.example', { user: '' })).toThrow(TypeError)
  })
})
