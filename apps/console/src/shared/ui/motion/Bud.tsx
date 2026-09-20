import { useReducedMotion } from '../../theme/motion'
import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { MORPH_FAMILY, familySpring } from './vocabulary'

const budStates = {
  folded: { opacity: 0, scaleY: .12, borderRadius: 'var(--radius-pill)' },
  expanded: { opacity: 1, scaleY: 1, borderRadius: 'var(--radius-md)' },
}
export function Bud({ from = 'bottom', className, children }: { from?: 'top' | 'bottom'; className?: string; children: ReactNode }) {
  const reduced = useReducedMotion()
  if (reduced) return <div className={className} data-bud="instant">{children}</div>
  const origin = { top: 0, bottom: 1 }[from]
  return <motion.div className={className} data-bud="grown" layout variants={budStates} initial="folded" animate="expanded" exit="folded"
    transition={familySpring(MORPH_FAMILY.spawn)} style={{ originY: origin, overflow: 'hidden' }}>{children}</motion.div>
}
