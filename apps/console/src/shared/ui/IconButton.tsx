import type { MouseEvent } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import { exprHeavy, physics } from '../theme/motion'
import { cx } from './cx'
import { ControlContent, ControlGlyph } from './controlContent'
import { activateControl, controlAvailability, controlMotion, controlTitle } from './controlState'

interface IconButtonProps {
  icon: LucideIcon; label: string; title?: string; onClick?: (event: MouseEvent) => void
  active?: boolean; filled?: boolean; size?: number; iconSize?: number; className?: string
  disabled?: boolean; disabledReason?: string; loading?: boolean; iconKey?: string; bloom?: boolean; tone?: 'neutral' | 'danger'
}
export function IconButton({ icon, label, title, onClick, active, filled, size = 40, iconSize = 20, className,
  disabled = false, disabledReason, loading = false, iconKey, bloom, tone = 'neutral' }: IconButtonProps) {
  const reduced = useReducedMotion()
  const state = controlAvailability(disabled, loading, disabledReason, true)
  const palette = disabled ? 'text-on-surface-var opacity-40 cursor-not-allowed'
    : filled ? 'bg-primary text-on-primary hover:bg-primary-emphasis'
      : active ? 'bg-surface-high text-on-surface'
        : tone === 'danger' ? 'text-on-surface-var hover:text-danger'
          : 'text-on-surface-var hover:bg-surface-high hover:text-on-surface'
  const celebrate = bloom && !reduced && !state.blocked
  return <motion.button type="button" aria-label={label} aria-pressed={active} aria-disabled={state.ariaDisabled}
    aria-busy={state.busy} title={controlTitle(title ?? label, disabled, disabledReason)}
    onClick={(event) => activateControl(event, state.blocked, onClick)}
    {...controlMotion(reduced, state.blocked, 0.08, 0.06)}
    animate={celebrate ? { scale: [1, 1.18, 1] } : undefined} transition={celebrate ? physics.playful : physics.snappy}
    style={{ width: size, height: size }}
    className={cx('group relative inline-flex shrink-0 items-center justify-center rounded-pill border border-transparent transition-colors duration-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
      palette, loading && !disabled && 'cursor-progress', className)}>
    {!state.blocked && !filled && !reduced && exprHeavy(0.5) && <span aria-hidden
      className="pointer-events-none absolute inset-0 rounded-pill opacity-0 ring-1 ring-inset ring-primary/25 transition-opacity group-hover:opacity-100"
      style={{ background: 'radial-gradient(ellipse at top, color-mix(in srgb, var(--color-primary) 16%, transparent), transparent 75%)' }} />}
    <ControlContent busy={loading} iconSize={iconSize} glyph><ControlGlyph icon={icon} size={iconSize} identity={iconKey} /></ControlContent>
  </motion.button>
}
