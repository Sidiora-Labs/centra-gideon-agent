import type { MouseEvent, ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { spring } from '../theme/motion'
import { cx } from './cx'
import { controlMotion } from './controlState'

export function TileButton({ children, onClick, active, title, ariaLabel, className }: {
  children: ReactNode; onClick?: (event: MouseEvent<HTMLButtonElement>) => void; active?: boolean
  title?: string; ariaLabel?: string; className?: string
}) {
  const reduced = useReducedMotion()
  const selected = active === true
  return <motion.button type="button" aria-label={ariaLabel} aria-pressed={active} title={title} onClick={onClick}
    {...controlMotion(reduced, false, 0.05)} transition={spring.spatialFast}
    className={cx('group relative flex flex-col overflow-hidden rounded-xl border bg-surface-container text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary',
      selected ? 'border-primary/60 ring-1 ring-inset ring-primary/15' : 'border-outline-variant/40 hover:border-outline-variant hover:bg-surface-high', className)}>
    {children}
  </motion.button>
}
