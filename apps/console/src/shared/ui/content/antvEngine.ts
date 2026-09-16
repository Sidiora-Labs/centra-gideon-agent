export interface InfographicInstance {
  render: (syntax: string) => void
  destroy: () => void
  on?: (event: string, listener: () => void) => void
}
export type InfographicCtor = new (options: { container: Element; width?: string | number; height?: string | number; theme?: string }) => InfographicInstance

export const INFOGRAPHIC_LOCAL_FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
export const INFOGRAPHIC_FONT_FAMILIES = ['Alibaba PuHuiTi', 'Source Han Sans', 'Source Han Serif', 'LXGW WenKai', '851tegakizatsu'] as const
let cachedEngine: Promise<InfographicCtor> | undefined

async function initializeEngine(): Promise<InfographicCtor> {
  const engine = await import('@antv/infographic')
  for (const fontFamily of INFOGRAPHIC_FONT_FAMILIES) {
    engine.registerFont({ fontFamily, name: fontFamily, baseUrl: '', fontWeight: {} })
  }
  engine.setDefaultFont(INFOGRAPHIC_LOCAL_FONT)
  return engine.Infographic as unknown as InfographicCtor
}
export function loadInfographicEngine(): Promise<InfographicCtor> {
  if (!cachedEngine) {
    cachedEngine = initializeEngine().catch(error => { cachedEngine = undefined; throw error })
  }
  return cachedEngine
}
