export type SurfaceLayer = 0 | 1 | 2
export const LAYER_CORE: SurfaceLayer = 0
export const LAYER_APP: SurfaceLayer = 1
export const LAYER_USER: SurfaceLayer = 2
const layerNames = ['core', 'app', 'user'] as const
const disabledValues = new Set(['0', 'false'])
let operatorLatch = false

export function setServerSafeSurfaces(enabled: boolean): void {
  operatorLatch ||= enabled
}

export function safeModeInUrl(hash = typeof window === 'undefined' ? '' : window.location.hash): boolean {
  const separator = hash.indexOf('?')
  if (separator < 0) return false
  const choice = new URLSearchParams(hash.slice(separator + 1)).get('safe')
  return choice !== null && !disabledValues.has(choice.toLowerCase())
}

export function safeMode(hash?: string): boolean {
  const documentFlag = typeof document !== 'undefined'
    && document.querySelector('meta[name="gideon-safe-surfaces"]')?.getAttribute('content') === '1'
  const levers = [operatorLatch, documentFlag, safeModeInUrl(hash)]
  return levers.some(Boolean)
}

export function maxSurfaceLayer(hash?: string): SurfaceLayer {
  return safeMode(hash) ? LAYER_CORE : LAYER_USER
}

export function layerName(layer: SurfaceLayer): string {
  return layerNames[layer] ?? 'user'
}
