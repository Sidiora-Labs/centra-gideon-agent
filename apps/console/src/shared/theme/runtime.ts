
export type DotShape = 'circle' | 'square' | 'diamond' | 'star' | 'sparkle' | 'burst' | 'claude'
export type DotPattern = 'grid' | 'diamond' | 'hex' | 'brick'

export const runtime = {
  glow: 1,
  animSpeed: 1,
  waveAmount: 1,
  surfaceAngle: 45,
  surfaceDistance: 1,
  dotSize: 1,
  dotDensity: 1,
  dotShape: 'claude' as DotShape,
  dotPattern: 'hex' as DotPattern,
  glowA: [255, 107, 91] as [number, number, number],
  glowB: [255, 154, 122] as [number, number, number],

  bounciness: 1,
  expressiveness: 0.8,
  dragElastic: 0.9,
  swipeVelocity: 500,
  swipeDistance: 80,
}
