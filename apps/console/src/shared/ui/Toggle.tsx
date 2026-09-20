import { motion } from 'framer-motion'
import { spring, useReducedMotion } from '../theme/motion'
import { useFieldHintId } from './forms'
import { controlAvailability } from './controlState'

const dimensions = { sm: { classes: 'h-5 w-9', knob: 14, travel: 16 }, md: { classes: 'h-6 w-10', knob: 16, travel: 18 } }
export function Toggle({ on, onChange, label, disabled = false, disabledReason, size = 'md', readOnly = false, decorative = false }: {
  on: boolean; onChange?: (value: boolean) => void; label?: string; disabled?: boolean; disabledReason?: string
  size?: 'sm' | 'md'; readOnly?: boolean; decorative?: boolean
}) {
  const hint = useFieldHintId()
  const reduced = useReducedMotion()
  const geometry = dimensions[size]
  const state = controlAvailability(disabled, false, disabledReason)
  const trackClass = `relative inline-flex shrink-0 items-center rounded-pill ring-1 ring-inset ring-outline-variant/20 transition-colors ${geometry.classes}`
  const background = { background: on ? 'var(--color-primary)' : 'var(--color-surface-highest)' }
  const knob = <motion.span aria-hidden className="ml-0.5 inline-block rounded-full shadow-sm"
    style={{ width: geometry.knob, height: geometry.knob, background: 'var(--color-on-primary)' }}
    initial={false} animate={{ x: on ? geometry.travel : 0 }} transition={reduced ? { duration: 0 } : spring.spatialFast} />
  if (readOnly || !onChange) {
    const semantics = decorative ? { 'aria-hidden': true as const }
      : { role: 'switch', 'aria-checked': on, 'aria-label': label, 'aria-describedby': hint }
    return <span {...semantics} className={trackClass} style={background}>{knob}</span>
  }
  return <button type="button" role="switch" aria-label={label} aria-checked={on}
    aria-describedby={state.ariaDisabled ? undefined : hint} aria-disabled={state.ariaDisabled} disabled={state.nativeDisabled}
    title={state.ariaDisabled ? disabledReason : undefined} onClick={() => { if (!state.blocked) onChange(!on) }}
    className={`inline-flex h-6 shrink-0 items-center justify-center rounded-pill focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-canvas disabled:cursor-not-allowed disabled:opacity-40 aria-disabled:cursor-not-allowed aria-disabled:opacity-40 ${size === 'sm' ? '-my-0.5' : ''}`}>
    <span className={trackClass} style={background}>{knob}</span>
  </button>
}
