import type { runtime, DotShape } from '../theme/runtime'

export interface GlowRect { cx: number; cy: number; halfW: number; halfH: number; radius?: number }
export type GlowStyle = 'waves' | 'still' | 'glow' | 'none'
export interface GlowDot { x: number; y: number; radius: number; color: string }
export interface GlowLights { primary: GlowRect | null; traveling: GlowRect | null; travel: number; intensity: number }

const blend = (from: number, to: number, weight: number) => from + (to - from) * weight
export function interpolateGlow(from: GlowRect, to: GlowRect, weight: number): GlowRect {
  return {
    cx: blend(from.cx, to.cx, weight), cy: blend(from.cy, to.cy, weight),
    halfW: blend(from.halfW, to.halfW, weight), halfH: blend(from.halfH, to.halfH, weight),
    radius: blend(from.radius ?? 0, to.radius ?? 0, weight),
  }
}
export class GlowTravel {
  private held: GlowRect | null = null
  private strength = 0
  constructor(private intensity: number) {}
  advance(primary: GlowRect | null, destination: GlowRect | null, intensity: number): GlowLights {
    this.intensity = blend(this.intensity, intensity, 0.12)
    this.strength = blend(this.strength, destination ? 1 : 0, 0.08)
    if (destination) this.held = this.held ? interpolateGlow(this.held, destination, 0.14) : primary ?? destination
    else if (this.strength < 0.02) this.held = null
    return { primary, traveling: this.strength > 0.02 ? this.held : null, travel: this.strength, intensity: this.intensity }
  }
}

export function glowMode(style: GlowStyle, reduced: boolean) {
  return { visible: style !== 'none', dots: style === 'waves' || style === 'still', animate: !reduced && (style === 'waves' || style === 'glow') }
}

export function localGlowRect(
  source: Pick<DOMRect, 'left' | 'top' | 'width' | 'height'>,
  canvas: Pick<DOMRect, 'left' | 'top'>,
  zoom: number,
  radius: number,
): GlowRect {
  const scale = zoom > 0 && Number.isFinite(zoom) ? zoom : 1
  return { cx: (source.left - canvas.left + source.width / 2) / scale,
    cy: (source.top - canvas.top + source.height / 2) / scale,
    halfW: source.width / (2 * scale), halfH: source.height / (2 * scale), radius: radius / scale }
}

export function glowProximity(x: number, y: number, light: GlowRect, reach: number) {
  if (reach <= 0) return 0
  const dx = Math.max(0, Math.abs(x - light.cx) - light.halfW)
  const dy = Math.max(0, Math.abs(y - light.cy) - light.halfH)
  return Math.max(0, 1 - Math.hypot(dx, dy) / reach) ** 3.4
}

export function* glowField(width: number, height: number, milliseconds: number, settings: typeof runtime, lights: GlowLights): Generator<GlowDot> {
  if (width <= 0 || height <= 0 || settings.dotDensity <= 0) return
  const columns = Math.max(1, Math.round(150 * settings.dotDensity))
  const rows = Math.max(2, Math.round(130 * settings.dotDensity))
  const time = milliseconds * settings.animSpeed / 1000
  const angle = settings.surfaceAngle * Math.PI / 180
  const rotation = [Math.cos(angle), Math.sin(angle)]
  const focal = 0.575 * height
  const far = 40 * settings.surfaceDistance
  const reach = Math.min(width, height) * 0.18
  for (let row = rows - 1; row >= 0; row--) {
    const depth = 0.02 + (far - 0.02) * (row / (rows - 1)) ** 2
    const staggered = row % 2 === 1 && settings.dotPattern !== 'grid'
    const shift = staggered ? 30 / columns : 0
    for (let column = 0; column <= columns; column++) {
      if (settings.dotPattern === 'hex' && staggered && column % 2 === 0) continue
      const worldX = column * 60 / columns - 30 + shift + Math.sin(depth * 0.4 + time * 0.2) * 1.6
      const wave = (Math.sin(worldX * 0.22 + depth * 0.18 + time * 0.5)
        + Math.sin(worldX * 0.12 - depth * 0.26 - time * 0.32)) / 2
      const vertical = 3.4 - wave * settings.waveAmount
      const cameraDepth = depth * rotation[0] + vertical * rotation[1]
      if (cameraDepth <= 0.12) continue
      const scale = focal / cameraDepth
      const x = width / 2 + worldX * scale
      const y = height * 0.32 + (vertical * rotation[0] - depth * rotation[1]) * scale
      if (x < -40 || x > width + 40 || y < -40 || y > height + 40) continue
      let proximity: number
      if (lights.primary || lights.traveling) {
        proximity = Math.max(lights.primary ? glowProximity(x, y, lights.primary, reach) : 0,
          lights.traveling ? glowProximity(x, y, lights.traveling, reach) * lights.travel * 0.55 : 0)
      } else {
        const distance = Math.hypot((x - width / 2) / (Math.min(width, 1100) * 0.6), (y - height / 2) / (height * 0.34))
        proximity = Math.max(0, 1 - distance) ** 1.7
      }
      if (proximity <= 0.01) continue
      const crest = (wave + 1) / 2
      const opacity = (0.08 + crest * 0.7) * proximity * lights.intensity * settings.glow
      if (opacity <= 0.012) continue
      const color = settings.glowA.map((channel, index) => Math.round(blend(channel, settings.glowB[index], crest)))
      yield { x, y, radius: Math.max(0.4, Math.min(9, scale * 0.022)) * (0.75 + crest * 0.5) * settings.dotSize,
        color: `rgba(${color.join(',')},${Math.min(0.95, opacity)})` }
    }
  }
}

type Vertex = [number, number]
export function glowPolygons(shape: DotShape, radius: number): Vertex[][] {
  const rotate = (points: Vertex[], angle: number): Vertex[] => points.map(([x, y]) => [x * Math.cos(angle) - y * Math.sin(angle), x * Math.sin(angle) + y * Math.cos(angle)])
  switch (shape) {
    case 'square': return [[[-radius, -radius], [radius, -radius], [radius, radius], [-radius, radius]]]
    case 'diamond': return [[[0, -radius], [radius, 0], [0, radius], [-radius, 0]]]
    case 'star': return [Array.from({ length: 10 }, (_, index) => {
      const length = radius * (index % 2 ? 0.45 : 1)
      const angle = -Math.PI / 2 + index * Math.PI / 5
      return [Math.cos(angle) * length, Math.sin(angle) * length]
    })]
    case 'burst': {
      const half = Math.max(0.6, radius * 0.5) / 2
      return Array.from({ length: 3 }, (_, index) => rotate([[-half, -radius], [half, -radius], [half, radius], [-half, radius]], index * Math.PI / 3))
    }
    case 'claude': {
      const half = Math.max(0.5, radius * 0.22)
      return Array.from({ length: 11 }, (_, index) => rotate([[-half, 0], [half, 0], [0, -radius * 1.15]], index * 2 * Math.PI / 11))
    }
    default: return []
  }
}

export function paintGlowDot(context: CanvasRenderingContext2D, shape: DotShape, dot: GlowDot) {
  const { x, y, radius } = dot
  if (!(radius > 0)) return
  context.fillStyle = dot.color
  if (shape === 'circle') {
    context.beginPath(); context.arc(x, y, radius, 0, Math.PI * 2); context.fill()
  } else if (shape === 'sparkle') {
    const inner = radius * 0.32
    const segments = [[inner, -inner, radius, 0], [inner, inner, 0, radius], [-inner, inner, -radius, 0], [-inner, -inner, 0, -radius]]
    context.beginPath(); context.moveTo(x, y - radius)
    segments.forEach(([cx, cy, dx, dy]) => context.quadraticCurveTo(x + cx, y + cy, x + dx, y + dy))
    context.closePath(); context.fill()
  } else for (const points of glowPolygons(shape, radius)) {
    context.beginPath()
    points.forEach(([dx, dy], index) => index ? context.lineTo(x + dx, y + dy) : context.moveTo(x + dx, y + dy))
    context.closePath(); context.fill()
  }
}
