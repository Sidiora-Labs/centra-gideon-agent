import type { ReactNode, MouseEvent } from 'react'
import type { LucideIcon } from 'lucide-react'
import { cx } from './cx'

type Size = 'xs' | 'sm' | 'inherit'
const SIZE_ROLE: Record<Size, string | undefined> = {
  xs: 'caption',
  sm: 'body-s',
  inherit: undefined,
}

type Ink = 'primary' | 'emphasis'
const INK: Record<Ink, string> = {
  primary: 'text-primary',
  emphasis: 'text-primary-emphasis',
}

export function TextLink({
  children, href, external = false, onClick, icon: Icon, iconPosition = 'leading',
  iconSize = 13, size = 'inherit', ink = 'primary', disabled = false, title, className,
  'aria-label': ariaLabel,
}: {
  children: ReactNode
  href?: string
  external?: boolean
  onClick?: (e: MouseEvent<HTMLElement>) => void
  icon?: LucideIcon
  iconPosition?: 'leading' | 'trailing'
  iconSize?: number
  size?: Size
  ink?: Ink
  disabled?: boolean
  title?: string
  className?: string
  'aria-label'?: string
}) {
  const cls = cx(
    INK[ink],
    'hover:underline disabled:opacity-50 py-1 -my-1',
    Icon && 'inline-flex items-center gap-1',
    className,
  )
  const role = SIZE_ROLE[size]
  const body = Icon
    ? (iconPosition === 'trailing'
        ? <>{children} <Icon size={iconSize} /></>
        : <><Icon size={iconSize} /> {children}</>)
    : children

  if (href !== undefined) {
    return (
      <a href={href} onClick={onClick} title={title} aria-label={ariaLabel} data-type={role} className={cls}
        {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>
        {body}
      </a>
    )
  }
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title} aria-label={ariaLabel} data-type={role} className={cls}>
      {body}
    </button>
  )
}
