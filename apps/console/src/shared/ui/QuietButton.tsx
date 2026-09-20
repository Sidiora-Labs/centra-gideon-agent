import type { MouseEvent, ReactNode } from 'react'
import { motion } from 'framer-motion'
import { spring, useReducedMotion } from '../theme/motion'
import { cx } from './cx'
import { activateControl, controlAvailability, controlMotion, controlTitle } from './controlState'

export function QuietButton({ children, onClick, onDoubleClick, title, ariaExpanded, disabled = false, disabledReason, className }: {
  children: ReactNode; onClick?: (event: MouseEvent<HTMLButtonElement>) => void
  onDoubleClick?: (event: MouseEvent<HTMLButtonElement>) => void; title?: string; ariaExpanded?: boolean
  disabled?: boolean; disabledReason?: string; className?: string
}) {
  const reduced = useReducedMotion()
  const state = controlAvailability(disabled, false, disabledReason, true)
  return <motion.button type="button" aria-disabled={state.ariaDisabled} aria-expanded={ariaExpanded}
    title={controlTitle(title, disabled, disabledReason)}
    onClick={(event) => activateControl(event, state.blocked, onClick)}
    onDoubleClick={(event) => activateControl(event, state.blocked, onDoubleClick)}
    {...controlMotion(reduced, state.blocked, 0.05)} transition={spring.spatialFast} data-type="caption"
    className={cx('inline-flex h-7 shrink-0 items-center gap-xs rounded-md border border-transparent px-s transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
      disabled ? 'text-on-surface-low opacity-40 cursor-not-allowed' : 'text-on-surface-low hover:bg-surface-high hover:text-on-surface', className)}>
    {children}
  </motion.button>
}
