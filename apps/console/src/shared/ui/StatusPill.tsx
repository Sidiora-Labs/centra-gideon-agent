import type { HTMLAttributes } from 'react'
import { cx } from './cx'

export type StatusPillTone = 'ok' | 'warn' | 'danger' | 'info' | 'primary' | 'neutral'
const tones: Record<StatusPillTone, string> = {
  ok: 'var(--color-ok)', warn: 'var(--color-warn)', danger: 'var(--color-danger)',
  info: 'var(--color-info)', primary: 'var(--color-primary)', neutral: 'var(--color-outline-variant)',
}

export function StatusPill({ tone, sized = true, pad = true, className, style, ...attributes }: HTMLAttributes<HTMLSpanElement> & {
  tone: StatusPillTone; sized?: boolean; pad?: boolean
}) {
  const ink = tones[tone]
  const appearance = { background: `color-mix(in srgb, ${ink} 16%, transparent)`, color: ink, ...style }
  return <span data-type={sized ? 'caption' : undefined} {...attributes}
    className={cx('inline-flex shrink-0 items-center rounded-pill font-medium leading-relaxed', pad && 'px-1.5', className)} style={appearance} />
}
