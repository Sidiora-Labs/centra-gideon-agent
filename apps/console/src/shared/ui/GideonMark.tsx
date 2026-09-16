import { useMode } from '../../app/shell/theme'
import { gideonAssets } from './gideonIdentity'
import { motion, useReducedMotion } from 'framer-motion'

export function GideonMark({ size = 24, animated = false, blob = false }: {
  size?: number
  animated?: boolean
  idGradient?: string
  blob?: boolean
}) {
  const { mode } = useMode()
  const reducedMotion = useReducedMotion()
  const moving = animated && !reducedMotion
  const mark = (
    <motion.img
      src={gideonAssets[mode].mark} width={size} height={size} alt="Gideon"
      animate={moving ? { opacity: [0.75, 1, 0.75], scale: [0.96, 1.04, 0.96] } : undefined}
      transition={moving ? { duration: 3.2, ease: 'easeInOut', repeat: Infinity } : undefined}
      style={{ display: 'block', objectFit: 'contain' }}
    />
  )
  if (!blob) return mark
  return (
    <motion.div
      style={{
        display: 'grid', placeItems: 'center', padding: Math.round(size * 0.42), borderRadius: '50%',
        background: 'radial-gradient(circle, color-mix(in srgb, var(--grad-2) 24%, transparent), transparent 70%)',
      }}
      animate={moving ? { scale: [1, 1.08, 1] } : undefined}
      transition={moving ? { duration: 4, ease: 'easeInOut', repeat: Infinity } : undefined}
    >
      {mark}
    </motion.div>
  )
}
