import type { ReactNode } from 'react'
import { cx } from './cx'

export function FormFooter({ children, className }: { children: ReactNode; className?: string }) {
  const layout = 'sticky bottom-0 z-10 -mx-l flex flex-wrap items-center justify-end gap-s px-l py-3'
  const surface = 'border-t border-outline-variant/40 bg-surface/95 backdrop-blur-sm'
  return <div className={cx(layout, surface, className)}>{children}</div>
}
