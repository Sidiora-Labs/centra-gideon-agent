import type { CSSProperties } from 'react'


export type FontWeight = 400 | 470 | 500 | 550 | 600 | 650

export function fvs(weight: FontWeight): CSSProperties {
  return { fontVariationSettings: `"wght" ${weight}` }
}

export function withWeight(style: CSSProperties | undefined, weight: FontWeight): CSSProperties {
  return { ...style, fontVariationSettings: `"wght" ${weight}` }
}
