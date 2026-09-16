import { useReducedMotion } from 'framer-motion'
import { cx } from './cx'
import { progressFraction } from './statusSurfaceState'

export function Meter({ label, pct, detail, tone = 'var(--color-primary)', size = 'default', className }: {
  label: string; pct: number; detail?: string; tone?: string; size?: 'thin' | 'default'; className?: string
}) {
  const reduced = useReducedMotion()
  const value = progressFraction(pct, 100) * 100
  const track = { thin: 'h-1', default: 'h-1.5' }[size]
  return <div className={cx('flex min-w-0 flex-col gap-xs', className)}>
    <div className={cx('w-full overflow-hidden rounded-pill bg-surface-high', track)}
      role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value)}>
      <div className={cx('h-full rounded-pill', !reduced && 'transition-[width] duration-300')}
        style={{ width: `${value}%`, background: tone }} />
    </div>
    {detail && <div data-type="caption" className="text-on-surface-low tabular-nums">{detail}</div>}
  </div>
}
