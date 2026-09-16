
export type ErrorTreatmentId = 'terminal-frame' | 'arcade-panel'

export interface ErrorTreatment {
  id: ErrorTreatmentId
  label: string
  surfaceClass: string
  iconClass: string
  paint: { bg: string; ink: string; icon: string }
}

export const ERROR_TREATMENTS: Record<ErrorTreatmentId, ErrorTreatment> = {
  'terminal-frame': {
    id: 'terminal-frame',
    label: 'Terminal frame',
    surfaceClass: 'font-mono tracking-wide rounded-none border border-danger',
    iconClass: 'text-danger',
    paint: { bg: '--color-surface-container', ink: '--color-on-surface', icon: '--color-danger' },
  },
  // Gideon Arcade: a dashed cabinet panel with a heavier frame — playful, still
  // unmistakably an error (the frame and glyph stay on the danger token).
  'arcade-panel': {
    id: 'arcade-panel',
    label: 'Arcade panel',
    surfaceClass: 'rounded-xl border-2 border-dashed border-danger',
    iconClass: 'text-danger',
    paint: { bg: '--color-surface-high', ink: '--color-on-surface', icon: '--color-danger' },
  },
}

export function getErrorTreatment(id: string | undefined): ErrorTreatment | null {
  if (!id || !Object.hasOwn(ERROR_TREATMENTS, id)) return null
  return ERROR_TREATMENTS[id as ErrorTreatmentId]
}

export function treatmentPaint(t: ErrorTreatment | null): { background: string; color: string } | null {
  if (!t) return null
  return { background: `var(${t.paint.bg})`, color: `var(${t.paint.ink})` }
}
