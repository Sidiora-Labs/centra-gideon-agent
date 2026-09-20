import type { ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Loader2, type LucideIcon } from 'lucide-react'
import { physics, spring, useReducedMotion } from '../theme/motion'

export function ControlContent({ busy, children, label, iconSize = 16, glyph = false }: {
  busy: boolean; children: ReactNode; label?: string; iconSize?: number; glyph?: boolean
}) {
  const reduced = useReducedMotion()
  const hidden = reduced ? { opacity: 0 } : { opacity: 0, scale: 0.75 }
  return <>
    <motion.span className="relative inline-flex items-center gap-s"
      animate={{ opacity: busy ? 0 : 1, ...(reduced ? {} : glyph ? { scale: busy ? 0.6 : 1 } : { y: busy ? -4 : 0 }) }}
      transition={spring.effects}>{children}</motion.span>
    <AnimatePresence>{busy && <motion.span aria-hidden className="pointer-events-none absolute inset-0 grid place-items-center px-s"
      initial={hidden} animate={{ opacity: 1, scale: 1 }} exit={hidden} transition={reduced ? spring.effects : physics.snappy}>
      <span className="inline-flex min-w-0 max-w-full items-center gap-s">
        <Loader2 size={iconSize} className="shrink-0 animate-spin" />
        {label && <span className="truncate">{label}</span>}
      </span>
    </motion.span>}</AnimatePresence>
  </>
}

export function ControlGlyph({ icon: Icon, size, identity }: { icon: LucideIcon; size: number; identity?: string }) {
  const reduced = useReducedMotion()
  const glyph = <Icon size={size} strokeWidth={2} absoluteStrokeWidth />
  if (!identity) return glyph
  const hidden = reduced ? { opacity: 0 } : { opacity: 0, scale: 0.55, rotate: -20 }
  return <AnimatePresence mode="wait" initial={false}>
    <motion.span key={identity} className="inline-flex" initial={hidden} exit={hidden}
      animate={{ opacity: 1, scale: 1, rotate: 0 }} transition={reduced ? spring.effects : physics.snappy}>{glyph}</motion.span>
  </AnimatePresence>
}
