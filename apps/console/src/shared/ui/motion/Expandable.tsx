import type { ReactNode } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { spring } from '../../theme/motion'

const disclosureStates = { closed: { height: 0, opacity: 0 }, open: { height: 'auto', opacity: 1 } }
export function Expandable({ open, header, children, className }: { open: boolean; header: ReactNode; children: ReactNode; className?: string }) {
  const reduced = useReducedMotion()
  const style = { overflow: 'hidden' }
  if (reduced) return <div className={className} style={style} data-expandable="instant">{header}{open && <div>{children}</div>}</div>
  return <motion.div className={className} style={style} data-expandable="animated" layout transition={spring.spatialDefault}>
    {header}<AnimatePresence initial={false}>{open && <motion.div key="body" variants={disclosureStates} initial="closed" animate="open" exit="closed" transition={spring.spatialDefault} style={style}>{children}</motion.div>}</AnimatePresence>
  </motion.div>
}
