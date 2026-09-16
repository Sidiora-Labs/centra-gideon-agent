import { describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { registerServiceWorker, serviceWorkerBlockedReason } from './registerServiceWorker'


describe('serviceWorkerBlockedReason', () => {
  it('names the missing platform feature', () => {
    expect(serviceWorkerBlockedReason({} as Navigator, { isSecureContext: true })).toMatch(
      /no service-worker support/,
    )
  })

  it('names a non-secure context, because that is the LAN-http case', () => {
    const nav = { serviceWorker: {} } as unknown as Navigator
    expect(serviceWorkerBlockedReason(nav, { isSecureContext: false })).toMatch(
      /not a secure context/,
    )
  })

  it('is null when the browser can run a worker', () => {
    const nav = { serviceWorker: {} } as unknown as Navigator
    expect(serviceWorkerBlockedReason(nav, { isSecureContext: true })).toBeNull()
  })
})

describe('registerServiceWorker', () => {
  it('does nothing in a dev build — no worker to shadow Vite HMR output', async () => {
    const register = vi.fn()
    vi.stubGlobal('navigator', { serviceWorker: { register } })
    await expect(registerServiceWorker(false)).resolves.toBeNull()
    expect(register).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('registers /sw.js at the root scope', async () => {
    const registration = { scope: '/' }
    const register = vi.fn().mockResolvedValue(registration)
    vi.stubGlobal('navigator', { serviceWorker: { register } })
    vi.stubGlobal('window', { isSecureContext: true })
    await expect(registerServiceWorker(true)).resolves.toBe(registration)
    expect(register).toHaveBeenCalledWith('/sw.js', { scope: '/' })
    vi.unstubAllGlobals()
  })

  it('survives a failed registration instead of taking the dashboard down', async () => {
    const register = vi.fn().mockRejectedValue(new Error('nope'))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('navigator', { serviceWorker: { register } })
    vi.stubGlobal('window', { isSecureContext: true })
    await expect(registerServiceWorker(true)).resolves.toBeNull()
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
    vi.unstubAllGlobals()
  })
})

describe('main.tsx wires registration', () => {
  it('calls registerServiceWorker at boot', () => {
    const main = readFileSync(join(__dirname, "../bootstrap/main.tsx"), 'utf8')
    expect(main).toContain("from '../shell/registerServiceWorker'")
    expect(main).toMatch(/registerServiceWorker\(\)/)
  })
})
