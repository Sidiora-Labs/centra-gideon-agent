import type { ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import { withWeight } from '../theme/fontWeight'
import { spring } from '../theme/motion'

export function FilterRow({ label, count, icon: Icon, selected, indicatorId, onClick, pressed, trailing }: {
  label: string; count?: number; icon?: LucideIcon; selected: boolean; indicatorId?: string
  onClick: () => void; pressed?: boolean; trailing?: ReactNode
}) {
  const reduced = useReducedMotion()
  const colors = { icon: selected ? 'var(--color-primary)' : 'var(--color-on-surface-var)', label: selected ? 'var(--color-primary)' : 'var(--color-on-surface)' }
  const showCount = typeof count === 'number' && count > 0
  return <motion.button type="button" aria-pressed={pressed} onClick={onClick}
    whileTap={reduced ? undefined : { scale: 0.98 }} transition={spring.spatialFast}
    className={`relative flex h-8 w-full items-center gap-s rounded-lg px-s text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${selected ? '' : 'hover:bg-surface-high'}`}>
    {selected && indicatorId && <motion.span aria-hidden layoutId={reduced ? undefined : indicatorId} transition={spring.spatialFast}
      className="absolute inset-0 rounded-lg border border-primary/15" style={{ background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)' }} />}
    {Icon && <Icon size={14} aria-hidden className="relative shrink-0" style={{ color: colors.icon }} />}
    <span data-type="label-s" className="relative min-w-0 flex-1 truncate" style={withWeight({ color: colors.label }, selected ? 550 : 400)}>{label}</span>
    {showCount && <span data-type="caption" className="relative shrink-0 text-on-surface-low tabular-nums">{count}</span>}
    {trailing}
  </motion.button>
}
