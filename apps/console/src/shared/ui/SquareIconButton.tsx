import type { MouseEvent, ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import { spring } from '../theme/motion'
import { cx } from './cx'
import { ControlContent } from './controlContent'
import { activateControl, controlAvailability, controlMotion, controlTitle } from './controlState'

interface SquareIconButtonProps {
  icon?: LucideIcon; children?: ReactNode; label: string; title?: string; onClick?: (event: MouseEvent) => void
  on?: boolean; ariaExpanded?: boolean; disabled?: boolean; disabledReason?: string; loading?: boolean
  tone?: 'neutral' | 'danger'; iconSize?: number; className?: string
}
export function SquareIconButton({ icon: Icon, children, label, title, onClick, on, ariaExpanded, disabled = false,
  disabledReason, loading = false, tone = 'neutral', iconSize = 14, className }: SquareIconButtonProps) {
  const reduced = useReducedMotion()
  const state = controlAvailability(disabled, loading, disabledReason, true)
  const selected = !!(on || ariaExpanded)
  const palette = disabled ? 'text-on-surface-low opacity-40 cursor-not-allowed'
    : selected ? 'text-primary' : tone === 'danger' ? 'text-on-surface-low hover:text-danger'
      : 'text-on-surface-low hover:bg-surface-high hover:text-on-surface'
  return <motion.button type="button" aria-label={label} aria-expanded={ariaExpanded}
    aria-pressed={ariaExpanded === undefined ? on : undefined} aria-disabled={state.ariaDisabled} aria-busy={state.busy}
    title={controlTitle(title ?? label, disabled, disabledReason)} onClick={(event) => activateControl(event, state.blocked, onClick)}
    {...controlMotion(reduced, state.blocked, 0.1)} transition={spring.spatialFast}
    className={cx('relative grid size-7 shrink-0 place-items-center rounded-md border border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
      palette, loading && !disabled && 'cursor-progress', className)}
    style={selected && !disabled ? { background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' } : undefined}>
    <ControlContent busy={loading} iconSize={iconSize} glyph>{Icon ? <Icon size={iconSize} /> : children}</ControlContent>
  </motion.button>
}
