export const NO_READING = '—'

export function isReading(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function fixedReading(value: number | null | undefined, digits: number): string {
  return isReading(value) ? value.toFixed(digits) : NO_READING
}

export function percentReading(part: number | null | undefined, whole: number | null | undefined): number | null {
  if (!isReading(part) || !isReading(whole) || whole <= 0) return null
  const pct = part / whole * 100
  return isReading(pct) ? pct : null
}
