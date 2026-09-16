import type { ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { cx } from './cx'
import { spring, expr } from '../theme/motion'

export function AddItemButton({ children, onClick, className }: {
  children: ReactNode
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
  className?: string
}) {
  const reduce = useReducedMotion()
  const pressScale = reduce ? 1 : 1 - expr(0.05, 0.4)
  return (
    <motion.button
      type="button"
      onClick={onClick}
      whileTap={{ scale: pressScale }}
      transition={spring.spatialFast}
      data-type="body-s"
      className={cx(
        'inline-flex items-center gap-xs rounded-md bg-surface-container px-m h-9',
        'text-on-surface-var hover:bg-surface-high transition-colors',
        className,
      )}
    >
      {children}
    </motion.button>
  )
}
