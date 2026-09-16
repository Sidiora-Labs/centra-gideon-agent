export type RowWindow = { start: number; end: number }

export function recordRowHeight(heights: Map<string, number>, key: string, height: number): boolean {
  if (!Number.isFinite(height) || height <= 0) return false
  const previous = heights.get(key)
  if (previous !== undefined && Math.abs(previous - height) <= 0.5) return false
  heights.set(key, height)
  return true
}

export class RowGeometry {
  readonly offsets: Float64Array
  readonly positions: ReadonlyMap<string, number>
  readonly count: number
  readonly height: number

  constructor(
    keys: readonly string[],
    readonly estimate: number,
    readonly gap: number,
    measurements: ReadonlyMap<string, number>,
  ) {
    this.count = keys.length
    this.positions = new Map(keys.map((key, index) => [key, index]))
    this.offsets = new Float64Array(this.count + 1)
    keys.forEach((key, index) => {
      this.offsets[index + 1] = this.offsets[index] + (measurements.get(key) ?? estimate) + gap
    })
    this.height = this.count ? this.offsets[this.count] - gap : 0
  }

  private bound(value: number, inclusive: boolean): number {
    let left = 0
    let right = this.offsets.length
    while (left < right) {
      const middle = (left + right) >>> 1
      if (inclusive ? this.offsets[middle] <= value : this.offsets[middle] < value) left = middle + 1
      else right = middle
    }
    return left
  }

  visible(top: number, viewport: number, overscan: number): RowWindow {
    const first = Math.min(this.count, Math.max(0, this.bound(top, true) - 1))
    const last = Math.max(first, Math.min(this.count, this.bound(top + viewport, false)))
    return { start: Math.max(0, first - overscan), end: Math.min(this.count, last + overscan) }
  }

  around(index: number, viewport: number, overscan: number): RowWindow {
    return {
      start: Math.max(0, index - overscan),
      end: Math.min(this.count, index + Math.ceil(viewport / Math.max(1, this.estimate)) + overscan + 1),
    }
  }

  padding(range: RowWindow): { paddingTop: number; paddingBottom: number } {
    return {
      paddingTop: this.offsets[range.start],
      paddingBottom: Math.max(0, this.height - this.offsets[range.end] + this.gap),
    }
  }

  rowBottom(index: number): number { return this.offsets[index + 1] - this.gap }
}

export function listKeyDestination(key: string, current: number, count: number, page: number): number | undefined {
  if (!count) return undefined
  const origin = Math.max(0, current)
  const destinations: Record<string, number> = {
    ArrowDown: current + 1,
    ArrowUp: origin - 1,
    Home: 0,
    End: count - 1,
    PageDown: origin + page,
    PageUp: origin - page,
  }
  const destination = destinations[key]
  return destination === undefined ? undefined : Math.max(0, Math.min(count - 1, destination))
}
