import { useCallback, useId, useRef, type ReactNode, type Ref, type KeyboardEvent } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { Search, X } from 'lucide-react'
import { physics } from '../theme/motion'
import { cx } from './cx'

type SearchSize = 'sm' | 'md' | 'lg'
type SearchSurface = 'high' | 'container' | 'base'
const sizes = {
  sm: { box: 'h-8 rounded-md', text: 'body-s' },
  md: { box: 'h-9 rounded-md', text: 'body-s' },
  lg: { box: 'h-10 rounded-lg', text: 'body-m' },
} satisfies Record<SearchSize, { box: string; text: string }>
const surfaces: Record<SearchSurface, string> = { high: 'bg-surface-high', container: 'bg-surface-container', base: 'bg-surface' }

interface SearchFieldProps {
  value: string
  onChange: (value: string) => void
  ariaHasPopup?: 'listbox'
  ariaControls?: string
  ariaActiveDescendant?: string
  ariaExpanded?: boolean
  placeholder?: string
  ariaLabel?: string
  autoFocus?: boolean
  name?: string
  onKeyDown?: (event: KeyboardEvent<HTMLInputElement>) => void
  trailingSlot?: ReactNode
  clearable?: boolean
  variant?: 'overlay' | 'inline'
  size?: SearchSize
  surface?: SearchSurface
  inlineIconSize?: number
  clearOnEscape?: boolean
  inputRef?: Ref<HTMLInputElement>
  spellCheck?: boolean
  autoCapitalize?: string
  autoCorrect?: string
  onFocus?: () => void
}

export function SearchField(props: SearchFieldProps) {
  const { value, onChange, variant = 'overlay', size = 'lg', surface = 'high', clearable = true } = props
  const generated = useId()
  const element = useRef<HTMLInputElement | null>(null)
  const bind = useCallback((node: HTMLInputElement | null) => {
    element.current = node
    if (typeof props.inputRef === 'function') return props.inputRef(node)
    if (props.inputRef) props.inputRef.current = node
  }, [props.inputRef])
  const reduced = useReducedMotion()
  const inline = variant === 'inline'
  const name = props.name ?? `search-${generated}`
  const label = props.ariaLabel ?? props.placeholder ?? 'Search'
  const clear = () => {
    onChange('')
    element.current?.focus()
  }
  const keyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    props.onKeyDown?.(event)
    if (event.defaultPrevented || event.key !== 'Escape' || !value || (inline && !props.clearOnEscape)) return
    event.preventDefault()
    event.stopPropagation()
    clear()
  }
  const input = (
    <input ref={bind} type="search" id={name} name={name} value={value}
      onChange={(event) => onChange(event.target.value)} onKeyDown={keyDown} onFocus={props.onFocus}
      placeholder={props.placeholder} aria-label={label} autoFocus={props.autoFocus}
      spellCheck={props.spellCheck} autoCapitalize={props.autoCapitalize} autoCorrect={props.autoCorrect}
      aria-haspopup={props.ariaHasPopup} aria-controls={props.ariaControls}
      aria-activedescendant={props.ariaActiveDescendant} aria-expanded={props.ariaExpanded}
      data-type={sizes[size].text}
      className={cx('min-w-0 text-on-surface outline-none placeholder:text-on-surface-low [&::-webkit-search-cancel-button]:hidden',
        inline ? 'flex-1 bg-transparent' : cx('w-full border border-outline-variant/40 pl-9 pr-9 focus:ring-2 focus:ring-inset focus:ring-primary', sizes[size].box, surfaces[surface]))} />
  )
  const trailing = <>
    <AnimatePresence>{clearable && value !== '' && <motion.button key="clear" type="button" aria-label={`Clear ${label.toLowerCase()}`}
      onClick={clear} initial={{ opacity: 0, scale: reduced ? 1 : 0.8 }} animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: reduced ? 1 : 0.8 }} transition={physics.snappy}
      className={cx('grid shrink-0 place-items-center rounded-md text-on-surface-low hover:bg-surface-highest hover:text-on-surface', inline ? 'size-5' : 'size-6')}>
      <X size={inline ? 13 : 14} aria-hidden />
    </motion.button>}</AnimatePresence>
    {props.trailingSlot}
  </>
  if (inline) return <>
    <Search size={props.inlineIconSize ?? 14} aria-hidden className="pointer-events-none shrink-0 text-on-surface-low" />
    {input}{trailing}
  </>
  return <div className="relative w-full">
    <Search size={14} aria-hidden className="pointer-events-none absolute left-3 top-1/2 z-10 -translate-y-1/2 text-on-surface-low" />
    {input}
    <div className="absolute right-2.5 top-1/2 flex -translate-y-1/2 items-center gap-1">{trailing}</div>
  </div>
}
