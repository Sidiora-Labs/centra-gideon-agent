import { forwardRef } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { Composer } from './Composer'
import { physics, expr } from '../theme/motion'
import type { ComposerProps } from './composer/types'

export const ComposerStage = forwardRef<HTMLDivElement, ComposerProps>(function ComposerStage(props, ref) {
  const reduced = useReducedMotion()
  return <motion.div ref={ref} layoutId="composer-stage" data-gideon-composer-stage="true"
    transition={reduced ? { duration: 0 } : { ...physics.fluid, stiffness: 200 + expr(80, 0.4) }}
    className="relative z-10 isolate w-full min-w-0" style={{ maxWidth: 'var(--content-width)' }}>
    <Composer {...props} />
  </motion.div>
})
