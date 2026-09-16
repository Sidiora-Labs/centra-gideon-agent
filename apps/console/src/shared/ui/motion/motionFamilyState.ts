export type LiquidContour = 'circle' | 'squircle' | 'blob'
export interface MenuPoint { x: number; y: number }
export function boundedMenuPoint(point: MenuPoint, viewport: { width: number; height: number }, size: { width: number; height: number }): MenuPoint {
  const limit = (value: number, extent: number, span: number) => Math.max(8, Math.min(value, Math.max(8, extent - span - 8)))
  return { x: limit(point.x, viewport.width, size.width), y: limit(point.y, viewport.height, size.height) }
}
export class ActivationCompletion {
  private active = false
  private complete = false
  update(active: boolean) { if (active !== this.active) { this.active = active; this.complete = false } }
  finish(): boolean {
    if (!this.active || this.complete) return false
    this.complete = true
    return true
  }
}
export function reconcileReorder<Item>(items: Item[], proposal: Item[], getKey: (item: Item) => string, canDrag: (item: Item) => boolean): Item[] {
  const movable = items.filter(canDrag)
  const available = new Map(movable.map(item => [getKey(item), item]))
  const queue: Item[] = []
  for (const item of [...proposal, ...movable]) {
    const key = getKey(item)
    if (!available.has(key)) continue
    queue.push(available.get(key)!)
    available.delete(key)
  }
  let position = 0
  return items.map(item => canDrag(item) ? queue[position++] : item)
}

const tau = Math.PI * 2
const character: Record<LiquidContour, (angle: number) => number> = {
  circle: () => 0,
  squircle: angle => Math.pow(Math.pow(Math.cos(angle), 4) + Math.pow(Math.sin(angle), 4), -.25) - 1,
  blob: angle => .19 * Math.sin(3 * angle) + .11 * Math.cos(5 * angle),
}
type Point = readonly [number, number]
const coordinate = (point: Point) => point.map(value => value.toFixed(2)).join(' ')
const tangentControl = (point: Point, ahead: Point, behind: Point, direction: number): Point => [
  point[0] + direction * (ahead[0] - behind[0]) / 6,
  point[1] + direction * (ahead[1] - behind[1]) / 6,
]
export function liquidOutline(from: LiquidContour, to: LiquidContour, progress: number, phase: number, amplitude: number, breathe: number): string {
  const points: Point[] = Array.from({ length: 16 }, (_, index) => {
    const angle = index * tau / 16
    const departure = character[from](angle) + (character[to](angle) - character[from](angle)) * progress
    const radius = 34 * (1 + departure * amplitude + breathe * Math.sin(phase + angle * 2))
    return [50 + radius * Math.cos(angle), 50 + radius * Math.sin(angle)]
  })
  const at = (index: number) => points[(index + points.length) % points.length]
  const segments = points.map((point, index) => {
    const next = at(index + 1)
    const first = tangentControl(point, next, at(index - 1), 1)
    const second = tangentControl(next, at(index + 2), point, -1)
    return `C${coordinate(first)} ${coordinate(second)} ${coordinate(next)}`
  })
  return `M${coordinate(points[0])}${segments.join('')}Z`
}
