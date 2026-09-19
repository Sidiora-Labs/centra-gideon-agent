import type { HTMLAttributes } from 'react'
import { cx } from './cx'

export function MetaChip({ uppercase = false, className, ...attributes }: HTMLAttributes<HTMLSpanElement> & {
  uppercase?: boolean
}) {
  return <span data-type="caption" {...attributes}
    className={cx('inline-flex shrink-0 items-center gap-1 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low',
      uppercase && 'uppercase tracking-wide', className)} />
}
