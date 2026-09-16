
const ART_TOKENS = [
  '--color-primary',
  '--color-secondary',
  '--color-info',
  '--color-ok',
  '--color-warn',
  '--color-danger',
] as const

export function artHash(name: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

export function artStops(name: string): { from: string; to: string; angle: number } {
  const h = artHash(name)
  const n = ART_TOKENS.length
  const a = h % n
  const b = (a + 1 + ((h >>> 5) % (n - 1))) % n
  return { from: ART_TOKENS[a], to: ART_TOKENS[b], angle: 100 + ((h >>> 11) % 8) * 20 }
}

export function artGradient(name: string): string {
  const { from, to, angle } = artStops(name)
  return `linear-gradient(${angle}deg, `
    + `color-mix(in srgb, var(${from}) 30%, transparent), `
    + `color-mix(in srgb, var(${to}) 12%, transparent))`
}
