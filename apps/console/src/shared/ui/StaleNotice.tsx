import { useReducedMotion } from '../theme/motion'
import { RefreshCw } from 'lucide-react'
import {  } from 'framer-motion'
import { cx } from './cx'

export function StaleNotice({ stale, what, className, announce = true }: {
  stale: boolean; what: string; className?: string; announce?: boolean
}) {
  const reduced = useReducedMotion()
  return stale ? <span data-stale="true" role={announce ? 'status' : undefined} data-type="caption"
    className={cx('inline-flex items-center gap-1 rounded-md text-on-surface-low', className)}>
    <RefreshCw size={11} aria-hidden className={reduced ? undefined : 'animate-pulse'} />
    <span>Updating {what}…</span>
  </span> : null
}
