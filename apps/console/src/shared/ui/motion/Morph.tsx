import { useReducedMotion } from '../../theme/motion'
import type { CSSProperties, ReactNode } from 'react'
import { motion } from 'framer-motion'
import { MORPH_FAMILY, familySpring } from './vocabulary'

export function Morph({ id, className, style, children }: { id: string; className?: string; style?: CSSProperties; children: ReactNode }) {
  const reduced = useReducedMotion()
  const frame = { className, style }
  return reduced
    ? <div {...frame} data-morph="none">{children}</div>
    : <motion.div {...frame} data-morph="shared" layoutId={id} transition={familySpring(MORPH_FAMILY.flight)}>{children}</motion.div>
}
