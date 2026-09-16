import { beforeEach, describe, expect, it, vi } from 'vitest'
import { enableNativePush, nativeBridge, watchNativePushTaps } from './nativePush'

vi.mock('../../shared/data/api', () => ({
  api: {
    pushRelayRegister: vi.fn().mockResolvedValue({ ok: true, device_id: 'd1' }),
    pushRelayUnregister: vi.fn().mockResolvedValue({ ok: true }),
  },
}))

import { api } from '../../shared/data/api'

type Listener = (payload: unknown) => void

function fakeBridge({ platform = 'ios', permission = 'granted', token = 'tok-1' } = {}) {
  const listeners = new Map<string, Listener>()
  const plugin = {
    requestPermissions: vi.fn().mockResolvedValue({ receive: permission }),
    register: vi.fn().mockImplementation(async () => {
      listeners.get('registration')?.({ value: token })
    }),
    addListener: vi.fn().mockImplementation((name: string, cb: Listener) => {
      listeners.set(name, cb)
      return Promise.resolve({ remove: () => listeners.delete(name) })
    }),
  }
  const win = {
    Capacitor: {
      isNativePlatform: () => true,
      getPlatform: () => platform,
      Plugins: { PushNotifications: plugin },
    },
  }
  return { win, plugin, listeners }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('nativeBridge', () => {
  it('is null outside the shell, on non-store platforms, and without the plugin', () => {
    expect(nativeBridge({})).toBeNull()
    expect(
      nativeBridge({ Capacitor: { isNativePlatform: () => false, getPlatform: () => 'ios' } }),
    ).toBeNull()
    const { win } = fakeBridge({ platform: 'web' })
    expect(nativeBridge(win)).toBeNull()
    expect(
      nativeBridge({
        Capacitor: { isNativePlatform: () => true, getPlatform: () => 'ios', Plugins: {} },
      }),
    ).toBeNull()
  })

  it('yields the platform and plugin inside the shell', () => {
    const { win, plugin } = fakeBridge({ platform: 'android' })
    const bridge = nativeBridge(win)
    expect(bridge?.platform).toBe('android')
    expect(bridge?.plugin).toBe(plugin)
  })
})

describe('enableNativePush', () => {
  it('registers the OS token against this device id with the real platform', async () => {
    const { win, plugin, listeners } = fakeBridge({ platform: 'android', token: 'fcm-tok' })
    const result = await enableNativePush(win)
    expect(result).toEqual({ ok: true })
    expect(plugin.register).toHaveBeenCalledOnce()
    expect(api.pushRelayRegister).toHaveBeenCalledOnce()
    const [deviceId, platform, token] = vi.mocked(api.pushRelayRegister).mock.calls[0]
    expect(deviceId).toBeTruthy()
    expect(platform).toBe('android')
    expect(token).toBe('fcm-tok')
    expect(listeners.size, 'registration listeners are released after a token is delivered').toBe(0)
  })

  it('attaches the token listener before register fires', async () => {
    const { win, plugin } = fakeBridge()
    const result = await enableNativePush(win)
    expect(result.ok).toBe(true)
    const attachOrder = plugin.addListener.mock.invocationCallOrder[0]
    const registerOrder = plugin.register.mock.invocationCallOrder[0]
    expect(attachOrder).toBeLessThan(registerOrder)
  })

  it('names a permission refusal without touching the gateway', async () => {
    const { win } = fakeBridge({ permission: 'denied' })
    expect(await enableNativePush(win)).toEqual({ ok: false, reason: 'denied' })
    expect(api.pushRelayRegister).not.toHaveBeenCalled()
  })

  it('names the missing bridge', async () => {
    expect(await enableNativePush({})).toEqual({ ok: false, reason: 'no-bridge' })
  })

  it('surfaces a registrationError as a named failure', async () => {
    const { win, plugin, listeners } = fakeBridge()
    plugin.register.mockImplementation(async () => {
      listeners.get('registrationError')?.({ error: 'the vendor push service said no' })
    })
    const result = await enableNativePush(win)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.detail).toContain('the vendor push service said no')
    expect(api.pushRelayRegister).not.toHaveBeenCalled()
  })
})

describe('watchNativePushTaps', () => {
  it('routes an approval tap from the ids-only ping', () => {
    const { win, listeners } = fakeBridge()
    const onItem = vi.fn()
    watchNativePushTaps(onItem, win)
    listeners.get('pushNotificationActionPerformed')?.({
      notification: { data: { kind: 'approval', item_id: 'apr-7' } },
    })
    expect(onItem).toHaveBeenCalledWith('approval', 'apr-7')
  })

  it('ignores a tap whose ping carries no ids', () => {
    const { win, listeners } = fakeBridge()
    const onItem = vi.fn()
    watchNativePushTaps(onItem, win)
    listeners.get('pushNotificationActionPerformed')?.({ notification: { data: {} } })
    listeners.get('pushNotificationActionPerformed')?.({})
    expect(onItem).not.toHaveBeenCalled()
  })

  it('is a no-op outside the shell', () => {
    expect(() => watchNativePushTaps(vi.fn(), {})).not.toThrow()
  })
})
