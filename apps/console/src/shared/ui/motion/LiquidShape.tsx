import { useEffect, useId } from 'react'
import { animate, motion, useMotionValue, useTransform } from 'framer-motion'
import { expr, exprHeavy, useReducedMotion } from '../../theme/motion'
import { MORPH_FAMILY, familySpring } from './vocabulary'
import { liquidOutline, type LiquidContour } from './motionFamilyState'

export type LiquidShapeName = LiquidContour
const TUNING = { breathe: .055, breatheCycle: 6.2, fillCore: .82, fillEdge: .4 }
type ContourProps = { from: LiquidShapeName; to: LiquidShapeName; active: boolean; amplitude: number; breathe: number; fill: string }
function AnimatedContour({ from, to, active, amplitude, breathe, fill }: ContourProps) {
  const progress = useMotionValue(active ? 1 : 0)
  const phase = useMotionValue(0)
  const outline = useTransform([progress, phase], ([position, angle]: number[]) => liquidOutline(from, to, position, angle, amplitude, breathe))
  useEffect(() => {
    const controller = animate(progress, Number(active), familySpring(MORPH_FAMILY.state))
    return () => controller.stop()
  }, [active, progress])
  useEffect(() => {
    phase.set(0)
    if (!breathe) return
    const controller = animate(phase, Math.PI * 2, { duration: TUNING.breatheCycle, ease: 'linear', repeat: Infinity })
    return () => controller.stop()
  }, [phase, breathe])
  return <motion.path d={outline} fill={fill} />
}
export function LiquidShape({ from = 'circle', to = 'blob', active, intensity = 1, tint = 'var(--color-primary)', className }: {
  from?: LiquidShapeName; to?: LiquidShapeName; active: boolean; intensity?: number; tint?: string; className?: string
}) {
  const reduced = useReducedMotion()
  const heavy = exprHeavy()
  const amplitude = expr(1) * intensity
  const gradient = `liquid-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  const fill = `url(#${gradient})`
  return <svg viewBox="0 0 100 100" aria-hidden focusable="false" className={className} style={{ pointerEvents: 'none' }}
    data-liquid-shape={reduced ? 'instant' : 'morph'} data-liquid-tier={reduced ? 'reduced' : heavy ? 'bold' : 'refined'}>
    <defs><radialGradient id={gradient} cx="50%" cy="45%" r="62%">
      {[['0%', TUNING.fillCore], ['100%', TUNING.fillEdge]].map(([offset, opacity]) => <stop key={offset} offset={offset} stopColor={tint} stopOpacity={opacity} />)}
    </radialGradient></defs>
    {reduced ? <path d={liquidOutline(from, to, Number(active), 0, amplitude, 0)} fill={fill} />
      : <AnimatedContour from={from} to={to} active={active} amplitude={amplitude} breathe={heavy ? expr(TUNING.breathe) * intensity : 0} fill={fill} />}
  </svg>
}
