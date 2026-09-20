import { motion } from 'framer-motion'
import { spring, useReducedMotion } from '../theme/motion'
import { progressArc, progressFraction } from './statusSurfaceState'

export function ProgressRing({ pct, tone, size = 28, label }: { pct: number; tone: string; size?: number; label: string }) {
  const reduce = useReducedMotion()
  const { center, radius, circumference, offset } = progressArc(pct, size)
  const geometry = { cx: center, cy: center, r: radius, fill: 'none', strokeWidth: 2.5 }
  return <svg width={center * 2} height={center * 2} viewBox={`0 0 ${center * 2} ${center * 2}`} className="shrink-0"
    role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progressFraction(pct) * 100)}>
    <circle {...geometry} stroke="var(--color-surface-high)" />
    <motion.circle {...geometry} stroke={tone} strokeLinecap="round" strokeDasharray={circumference}
      initial={false} animate={{ strokeDashoffset: offset }} transition={reduce ? { duration: 0 } : spring.spatialSlow}
      transform={`rotate(-90 ${center} ${center})`} />
  </svg>
}
