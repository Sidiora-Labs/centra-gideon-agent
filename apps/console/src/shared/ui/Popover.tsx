import { useCallback, useEffect, useReducer, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { fvs } from '../theme/fontWeight'
import { overlayEnter, spring } from '../theme/motion'
import { returnFocus } from './focusNavigation'
import { claimPopup, enterPopup, keepPopupVisible, ownsPopupKeyboard, popupPosition, popupSide, type PopupSide } from './popupController'

interface PopupState { open: boolean; side: PopupSide; anchor: DOMRect | null }
type PopupAction = { type: 'close' } | { type: 'open'; side: PopupSide; anchor: DOMRect | null }
function popupReducer(state: PopupState, action: PopupAction): PopupState {
  return action.type === 'close' ? { ...state, open: false } : { open: true, side: action.side, anchor: action.anchor }
}

export function Popover({ trigger, children, align = 'left', width, placement = 'top', openSignal, portal = false }: {
  trigger: (open: boolean, toggle: () => void) => ReactNode
  children: (close: () => void) => ReactNode
  align?: 'left' | 'right'
  width?: number
  placement?: PopupSide
  openSignal?: number
  portal?: boolean
}) {
  const [state, dispatch] = useReducer(popupReducer, { open: false, side: placement, anchor: null })
  const anchor = useRef<HTMLDivElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const invoker = useRef<HTMLElement | null>(null)
  const seenSignal = useRef(openSignal ?? 0)
  const open = useCallback(() => {
    const rect = anchor.current?.getBoundingClientRect() ?? null
    invoker.current = anchor.current?.querySelector<HTMLElement>('button, [tabindex]') ?? null
    dispatch({ type: 'open', side: rect ? popupSide(rect, placement, window.innerHeight) : placement, anchor: rect })
  }, [placement])
  const close = useCallback((restore: boolean) => {
    dispatch({ type: 'close' })
    if (restore) returnFocus(invoker.current, menu.current, true)
  }, [])
  useEffect(() => {
    if (openSignal === undefined || openSignal === seenSignal.current) return
    seenSignal.current = openSignal
    if (openSignal > 0) open()
  }, [openSignal, open])
  useEffect(() => {
    if (!state.open) return
    const ownership = claimPopup()
    const outside = (event: MouseEvent) => {
      const target = event.target
      if (target instanceof Node && !anchor.current?.contains(target) && !menu.current?.contains(target)) close(false)
    }
    const keyboard = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || !ownership.current()) return
      event.preventDefault()
      event.stopImmediatePropagation()
      event.stopPropagation()
      close(true)
    }
    const scroll = () => close(false)
    document.addEventListener('mousedown', outside)
    document.addEventListener('keydown', keyboard)
    if (portal) window.addEventListener('scroll', scroll, true)
    return () => {
      ownership.release()
      document.removeEventListener('mousedown', outside)
      document.removeEventListener('keydown', keyboard)
      if (portal) window.removeEventListener('scroll', scroll, true)
    }
  }, [state.open, portal, close])
  useEffect(() => {
    if (!state.open || !portal || !menu.current) return
    keepPopupVisible(menu.current)
    enterPopup(menu.current)
  }, [state.open, state.anchor, portal])

  const positioned = portal && state.anchor ? popupPosition(state.anchor, state.side, align, width ?? 200) : {}
  const flyout = <AnimatePresence>{state.open && (!portal || state.anchor) && (
    <motion.div ref={menu} variants={overlayEnter} initial="initial" animate="animate" exit="exit"
      onKeyDown={portal ? (event) => {
        if (event.key === 'Tab' && !event.defaultPrevented && !ownsPopupKeyboard(menu.current)) close(true)
      } : undefined}
      className={portal
        ? 'glass fixed z-[var(--z-menu)] rounded-xl border border-outline-variant/40 p-s'
        : `glass absolute z-30 rounded-xl border border-outline-variant/40 p-s ${state.side === 'bottom' ? 'top-full mt-s' : 'bottom-full mb-s'} ${align === 'right' ? 'right-0' : 'left-0'}`}
      style={{ ...positioned, width, minWidth: 200, transformOrigin: `${state.side === 'bottom' ? 'top' : 'bottom'} ${align}` }}>
      {children(() => close(true))}
    </motion.div>
  )}</AnimatePresence>
  return <div ref={anchor} className="relative">
    {trigger(state.open, () => state.open ? close(false) : open())}
    {portal ? createPortal(flyout, document.body) : flyout}
  </div>
}

export function MenuRow({ icon, label, hint, selected, onClick, role, tabIndex, disabled }: {
  icon?: ReactNode
  label: string
  hint?: string
  selected?: boolean
  onClick?: () => void
  role?: 'menuitem' | 'menuitemradio' | 'option'
  tabIndex?: number
  disabled?: boolean
}) {
  const reduced = useReducedMotion()
  const selection = role === 'option' ? { 'aria-selected': !!selected }
    : role === 'menuitemradio' ? { 'aria-checked': !!selected } : {}
  return <motion.button type="button" role={role} tabIndex={tabIndex} {...selection}
    aria-disabled={disabled || undefined} onClick={() => { if (!disabled) onClick?.() }}
    whileTap={reduced ? undefined : { scale: 0.98 }} transition={spring.spatialFast}
    className="group flex w-full items-center gap-s rounded-lg px-m py-2 text-left text-on-surface transition-colors hover:bg-primary/10 aria-disabled:cursor-not-allowed aria-disabled:opacity-40">
    {icon && <span className="shrink-0 text-on-surface-var">{icon}</span>}
    <span className="min-w-0 flex-1">
      <span data-type="label-s" className="block truncate" style={fvs(selected ? 500 : 400)}>{label}</span>
      {hint && <span data-type="caption" className="block truncate text-on-surface-low">{hint}</span>}
    </span>
    {selected && <span className="size-1.5 shrink-0 rounded-full bg-primary" />}
  </motion.button>
}
