import type { ReactNode } from 'react'
import { cx } from './cx'

const TONE = {
  muted: 'text-on-surface-low',
  info: 'text-info',
  primary: 'text-primary',
} as const

export function Eyebrow({
  children,
  as = 'div',
  tone = 'muted',
  id,
  className,
}: {
  children: ReactNode
  as?: 'div' | 'span' | 'p' | 'h2' | 'h3'
  tone?: keyof typeof TONE
  id?: string
  className?: string
}) {
  const Tag = as
  return (
    <Tag id={id} data-type="caption" className={cx(TONE[tone], className)}>
      {children}
    </Tag>
  )
}
