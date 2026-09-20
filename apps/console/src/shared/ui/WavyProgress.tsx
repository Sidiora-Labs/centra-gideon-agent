import { useReducedMotion } from '../theme/motion'
import { motion } from 'framer-motion'
import { progressFraction, progressWave } from './statusSurfaceState'

type WavyProgressProps = { width?: number; color?: string } & ({ value: number; label: string } | { value?: undefined; label?: never })
export function WavyProgress({ width = 120, color = 'var(--color-primary)', value, label }: WavyProgressProps) {
  const reduced = useReducedMotion()
  const measured = value !== undefined
  const fraction = measured ? progressFraction(value) : 0.6
  const path = { d: progressWave(width), stroke: color, strokeWidth: 2.5, strokeLinecap: 'round' as const }
  return <svg width={width} height={8} viewBox={`0 0 ${width} 8`} fill="none"
    role={measured ? 'progressbar' : undefined} aria-hidden={measured ? undefined : true}
    aria-label={label} aria-valuemin={measured ? 0 : undefined} aria-valuemax={measured ? 100 : undefined}
    aria-valuenow={measured ? Math.round(fraction * 100) : undefined}>
    {measured && <path {...path} opacity={0.2} />}
    <motion.path {...path} initial={false} style={{ pathLength: fraction }}
      animate={measured ? { pathLength: fraction } : reduced ? { pathOffset: 0 } : { pathOffset: [0, 1] }}
      transition={reduced ? { duration: 0 } : measured ? { ease: 'easeOut', duration: 0.4 } : { duration: 1.4, ease: 'linear', repeat: Infinity }} />
  </svg>
}
