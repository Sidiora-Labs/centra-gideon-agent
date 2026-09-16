import type { DesktopCapabilityWire } from './api'

export interface DesktopGrantResult {
  granted: boolean
  state: DesktopCapabilityWire['granted']
  prompted: boolean
  reason: string
}

export interface ChordBindResult {
  ok: boolean
  chord: string
  conflict: boolean
  reason: string
}

export interface NativeNotifyResult {
  ok: boolean
  route: string
  reason?: string
}

export interface LoginItemState {
  enabled: boolean
  supported: boolean
  describes: string
}

export interface LoginItemResult {
  ok: boolean
  enabled: boolean
  changed: boolean
  supported: boolean
  reason?: string
}

export interface DesktopBridge {
  onStatus?: (cb: (msg: string) => void) => () => void
  pushToTalk?: {
    bind: (chord: string) => Promise<ChordBindResult>
    setCapturing: (on: boolean) => Promise<boolean>
    on: (cb: (push: { action: 'toggle' | 'stop'; reason?: string }) => void) => () => void
  }
  notifications?: {
    show: (note: { title: string; body: string; route: string }) => Promise<NativeNotifyResult>
    on: (cb: (payload: { route: string }) => void) => () => void
  }
  loginItem?: {
    get: () => Promise<LoginItemState>
    set: (enabled: boolean) => Promise<LoginItemResult>
  }
  capabilities: {
    names: () => string[]
    probe: (cap: string) => Promise<DesktopCapabilityWire>
    snapshot: () => Promise<Record<string, DesktopCapabilityWire>>
    request: (cap: string) => Promise<DesktopGrantResult>
    on: (cap: string, cb: (state: DesktopCapabilityWire) => void) => () => void
  }
}

declare global {
  interface Window {
    gideonDesktop?: DesktopBridge
  }
}

export function desktopBridge(): DesktopBridge | null {
  return (typeof window !== 'undefined' && window.gideonDesktop) || null
}

export async function requestDesktopCapability(cap: string): Promise<DesktopGrantResult | null> {
  const bridge = desktopBridge()
  if (!bridge) return null
  try {
    return await bridge.capabilities.request(cap)
  } catch (e) {
    return { granted: false, state: 'unavailable', prompted: false, reason: e instanceof Error ? e.message : 'The desktop shell did not answer' }
  }
}

export async function getLoginItem(): Promise<LoginItemState | null> {
  const bridge = desktopBridge()
  if (!bridge?.loginItem) return null
  try {
    return await bridge.loginItem.get()
  } catch {
    return null
  }
}

export async function setLoginItem(enabled: boolean): Promise<LoginItemResult | null> {
  const bridge = desktopBridge()
  if (!bridge?.loginItem) return null
  try {
    return await bridge.loginItem.set(enabled)
  } catch (e) {
    return {
      ok: false,
      enabled: !enabled,
      changed: false,
      supported: true,
      reason: e instanceof Error ? e.message : 'The desktop app did not answer',
    }
  }
}
