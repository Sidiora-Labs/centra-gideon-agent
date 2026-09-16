import type { ReactNode } from 'react'
import { cx } from './cx'

type Tone = 'surface' | 'low' | 'container' | 'high'
const tones: Record<Tone, string> = {
  surface: 'bg-surface',
  low: 'bg-surface-low',
  container: 'bg-surface-container',
  high: 'bg-surface-high',
}

export function Surface({
  children, tone = 'container', radius = 'lg', className, glass, onClick,
}: {
  children: ReactNode
  tone?: Tone
  radius?: 'md' | 'lg' | 'xl' | 'squircle'
  className?: string
  glass?: boolean
  onClick?: () => void
}) {
  const r = radius === 'squircle' ? 'squircle'
    : radius === 'xl' ? 'rounded-xl' : radius === 'md' ? 'rounded-md' : 'rounded-lg'
  return (
    <div
      onClick={onClick}
      className={cx(glass ? 'glass' : tones[tone], r, className)}
    >
      {children}
    </div>
  )
}
