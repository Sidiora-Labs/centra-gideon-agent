
export const ESC_ESC_MS = 600

export interface EscapeDecision {
  forward: boolean
  lastEscAt: number
  release: boolean
}

export function escapeGate(key: string, at: number, lastEscAt: number, windowMs = ESC_ESC_MS): EscapeDecision {
  if (key !== 'Escape') return { forward: true, lastEscAt: 0, release: false }
  if (lastEscAt && at - lastEscAt < windowMs) return { forward: false, lastEscAt: 0, release: true }
  return { forward: true, lastEscAt: at, release: false }
}
