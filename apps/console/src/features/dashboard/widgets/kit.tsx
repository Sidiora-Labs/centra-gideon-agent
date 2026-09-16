import { type ReactNode } from 'react'
import { motion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import { cx } from '../../../shared/ui/cx'
import { spring } from '../../../shared/theme/motion'
import { RowHitTarget } from '../../../shared/ui/RowHitTarget'

export function SlotEmptyState({ icon: Icon, children, action }: { icon: LucideIcon; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex items-center gap-s self-start rounded-lg border border-dashed border-outline-variant/50 px-m py-s">
      <Icon size={15} className="shrink-0 text-on-surface-low opacity-70" />
      <p data-type="body-m" className="min-w-0 text-on-surface-low">{children}</p>
      {action && <div className="flex shrink-0 items-center" onClick={(e) => e.stopPropagation()}>{action}</div>}
    </div>
  )
}

export function SlotAction({ icon: Icon, children, onClick }: { icon: LucideIcon; children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-xs rounded-pill px-m py-xs text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface"
      data-type="label-m"
    >
      <Icon size={13} /> {children}
    </button>
  )
}

export function WidgetRow({
  onClick, label, children, actions, className,
}: {
  children: ReactNode
  actions?: ReactNode
  className?: string
} & (
  | { onClick: () => void; label: string }
  | { onClick?: undefined; label?: never }
)) {
  return (
    <motion.div
      layout
      transition={spring.spatialDefault}
      whileHover={onClick ? { y: -1 } : undefined}
      tabIndex={onClick ? -1 : undefined}
      className={cx(
        'flex items-center gap-s rounded-lg bg-surface-low px-m py-s',
        onClick && 'relative cursor-pointer transition-colors hover:bg-surface-high',
        onClick && 'has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary',
        className,
      )}
      onClick={onClick}
    >
      {onClick && <RowHitTarget label={label} />}
      <div className="min-w-0 flex-1">{children}</div>
      {actions && <div className="flex shrink-0 items-center gap-xs" onClick={(e) => e.stopPropagation()}>{actions}</div>}
    </motion.div>
  )
}

export function RowAction({
  onClick, children, tone = 'default', title, ariaLabel,
}: {
  onClick: () => void
  children: ReactNode
  tone?: 'default' | 'primary' | 'ok' | 'danger'
  title?: string
  ariaLabel?: string
}) {
  const toneCls = {
    default: 'text-on-surface-var hover:bg-surface-highest hover:text-on-surface',
    primary: 'text-primary-emphasis hover:bg-primary-container/40',
    ok: 'text-ok hover:bg-ok/15',
    danger: 'text-danger hover:bg-danger/15',
  }[tone]
  return (
    <motion.button
      type="button"
      title={title}
      aria-label={ariaLabel}
      whileTap={{ scale: 0.92 }}
      transition={spring.spatialFast}
      onClick={onClick}
      className={cx('inline-flex min-h-6 -my-px items-center gap-xs rounded-pill px-m py-xs transition-colors', toneCls)}
      data-type="label-m"
    >
      {children}
    </motion.button>
  )
}

export function StatusDot({ color, pulse }: { color: string; pulse?: boolean }) {
  return (
    <span className="relative inline-flex shrink-0" style={{ width: 8, height: 8 }}>
      {pulse && <span className="status-pulse absolute inset-0 rounded-pill" style={{ background: color }} />}
      <span className="relative inline-block rounded-pill" style={{ width: 8, height: 8, background: color }} />
    </span>
  )
}
