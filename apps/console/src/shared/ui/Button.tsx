import { useId, type MouseEvent, type ReactNode } from 'react'
import { motion, useMotionTemplate, useMotionValue } from 'framer-motion'
import { physics, exprHeavy, useReducedMotion } from '../theme/motion'
import { fvs } from '../theme/fontWeight'
import { cx } from './cx'
import { ControlContent } from './controlContent'
import { activateControl, controlAvailability, controlMotion, controlTitle, pointerPercent } from './controlState'

type Variant = 'primary' | 'tonal' | 'secondary' | 'ghost' | 'danger' | 'ghost-accent'
type Size = 'xs' | 'sm' | 'md' | 'lg'
const variants: Record<Variant, string> = {
  primary: 'bg-primary text-on-primary hover:bg-primary-emphasis',
  tonal: 'bg-primary/15 text-on-primary-tint hover:bg-primary/25',
  secondary: 'bg-surface-high text-on-surface hover:bg-surface-highest',
  ghost: 'bg-transparent text-on-surface hover:bg-surface-high',
  danger: 'bg-danger text-on-danger hover:opacity-90',
  'ghost-accent': 'bg-transparent text-primary-emphasis hover:bg-surface-high',
}
const sizes = {
  xs: { layout: 'h-7 px-m', type: 'label-s' }, sm: { layout: 'h-8 px-l', type: 'label-s' },
  md: { layout: 'h-10 px-xl', type: 'label-m' }, lg: { layout: 'h-12 px-2xl', type: 'label-l' },
}
interface ButtonProps {
  children: ReactNode; variant?: Variant; size?: Size; shape?: 'pill' | 'squircle'
  loading?: boolean; loadingLabel?: string; className?: string; onClick?: (event: MouseEvent<HTMLButtonElement>) => void
  disabled?: boolean; disabledReason?: string; type?: 'button' | 'submit'; title?: string
  ariaLabel?: string; ariaExpanded?: boolean; ariaPressed?: boolean
}

export function Button({ children, variant = 'primary', size = 'md', shape = 'pill', loading = false, loadingLabel,
  className, onClick, disabled = false, disabledReason, type = 'button', title, ariaLabel, ariaExpanded, ariaPressed }: ButtonProps) {
  const reduced = useReducedMotion()
  const state = controlAvailability(disabled, loading, disabledReason)
  const reasonId = useId()
  const pointerX = useMotionValue(50)
  const pointerY = useMotionValue(50)
  const sheen = useMotionTemplate`radial-gradient(ellipse at ${pointerX}% ${pointerY}%, color-mix(in srgb, var(--color-on-primary) 22%, transparent), transparent 65%)`
  const highlight = !state.blocked && !reduced && exprHeavy(0.45) && ['primary', 'danger'].includes(variant)
  const density = sizes[size]
  const description = loading && loadingLabel ? loadingLabel : disabled && disabledReason ? disabledReason : null
  return <><motion.button type={type} aria-label={ariaLabel}
    aria-describedby={description ? reasonId : undefined} aria-expanded={ariaExpanded} aria-pressed={ariaPressed}
    aria-busy={state.busy} aria-disabled={state.ariaDisabled} disabled={state.nativeDisabled}
    data-visual-state={loading ? 'loading' : disabled ? 'disabled' : 'ready'}
    title={controlTitle(title, !!state.ariaDisabled, disabledReason)}
    onClick={(event) => activateControl(event, state.blocked, onClick)}
    onPointerMove={(event) => {
      if (!highlight) return
      const rect = event.currentTarget.getBoundingClientRect()
      pointerX.set(pointerPercent(event.clientX, rect.left, rect.width))
      pointerY.set(pointerPercent(event.clientY, rect.top, rect.height))
    }}
    onPointerLeave={() => { pointerX.set(50); pointerY.set(50) }}
    {...controlMotion(reduced, state.blocked, 0.05, 0.025)} transition={physics.snappy}
    data-type={density.type} style={fvs(470)}
    className={cx('relative inline-flex shrink-0 select-none items-center justify-center gap-s overflow-hidden whitespace-nowrap border border-transparent transition-colors duration-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-canvas',
      shape === 'pill' ? 'rounded-pill' : 'squircle', variants[variant], density.layout,
      loading && loadingLabel ? 'disabled:pointer-events-none' : 'disabled:pointer-events-none disabled:opacity-40',
      'aria-disabled:cursor-not-allowed aria-disabled:opacity-40', className)}>
    {highlight && <motion.span aria-hidden className="pointer-events-none absolute inset-0" style={{ background: sheen }} />}
    <ControlContent busy={loading} label={loadingLabel}>{children}</ControlContent>
  </motion.button>{description && <span id={reasonId} className="sr-only">{description}</span>}</>
}
