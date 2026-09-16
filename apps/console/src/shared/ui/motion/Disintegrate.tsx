import { useEffect, useRef, useState, type ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { expr, exprHeavy } from '../../theme/motion'
import { familyFade, familyTween } from './vocabulary'
import { ActivationCompletion } from './motionFamilyState'

export function Disintegrate({ active, onDone, children, className }: { active: boolean; onDone?: () => void; children: ReactNode; className?: string }) {
  const reduced = useReducedMotion()
  const heavy = exprHeavy()
  const [completion] = useState(() => new ActivationCompletion())
  const [removed, setRemoved] = useState(false)
  const callback = useRef(onDone)
  callback.current = onDone
  completion.update(active)
  const finish = () => {
    if (!completion.finish()) return
    setRemoved(true)
    callback.current?.()
  }
  useEffect(() => {
    if (!active) { setRemoved(false); return }
    if (!reduced) return
    const timer = setTimeout(finish, 0)
    return () => clearTimeout(timer)
  }, [active, reduced, completion])
  if (reduced) return active && removed ? null : <div className={className}>{children}</div>
  const scatter = heavy ? { y: `${expr(8, 0)}%`, rotate: expr(3, 0), filter: `blur(${expr(2, 0)}px)` } : { y: '0%', rotate: 0 }
  const target = active
    ? { opacity: 0, height: 0, marginTop: 0, marginBottom: 0, ...scatter }
    : { opacity: 1, height: 'auto', y: 0, rotate: 0, ...(heavy ? { filter: 'blur(0px)' } : {}) }
  const mask = heavy ? 'linear-gradient(90deg, black 40%, transparent)' : undefined
  return <motion.div className={className} style={{ overflow: 'hidden', position: 'relative' }} animate={target}
    transition={active ? familyTween(heavy) : familyFade()} onAnimationComplete={finish}>
    {children}<motion.span aria-hidden className="pointer-events-none absolute inset-0" initial={{ opacity: 0 }} animate={{ opacity: active ? expr(.5, .4) : 0 }} transition={familyFade()}
      style={{ background: 'linear-gradient(90deg, transparent, color-mix(in srgb, var(--color-danger) 55%, transparent))', WebkitMaskImage: mask, maskImage: mask }} />
  </motion.div>
}
