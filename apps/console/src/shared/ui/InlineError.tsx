import { useReducedMotion } from '../theme/motion'
import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { AlertTriangle, X } from 'lucide-react'
import { cx } from './cx'

export function InlineError({ children, onDismiss, icon = false, multiline = false, animated = false, className, onRetry }: {
  children: ReactNode; onDismiss?: () => void; icon?: boolean; multiline?: boolean; animated?: boolean; className?: string; onRetry?: () => void
}) {
  const reduced = useReducedMotion()
  const actions = [
    ...(onRetry ? [{ name: 'Retry', run: onRetry, content: 'Retry' as ReactNode }] : []),
    ...(onDismiss ? [{ name: 'Dismiss', run: onDismiss, content: <X size={14} aria-hidden /> }] : []),
  ]
  const contents = <>
    {icon && <AlertTriangle size={14} aria-hidden className={cx('shrink-0', multiline && 'mt-0.5')} />}
    <span className={cx('min-w-0 flex-1', multiline && 'whitespace-pre-wrap break-words')}>{children}</span>
    {actions.map(action => <button key={action.name} type="button" aria-label={action.name} onClick={action.run}
      className="inline-flex min-h-6 min-w-6 shrink-0 items-center justify-center rounded-md px-1.5 font-medium hover:bg-danger/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger">{action.content}</button>)}
  </>
  const presentation = {
    role: 'alert', 'data-type': 'body-s',
    className: cx('flex gap-2 rounded-lg px-3 py-2', multiline ? 'items-start' : 'items-center', className),
    style: { background: 'color-mix(in srgb, var(--color-danger) 10%, transparent)', color: 'var(--color-danger)' },
  }
  return animated ? <motion.div {...presentation} initial={reduced ? false : { opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}>{contents}</motion.div>
    : <div {...presentation}>{contents}</div>
}
