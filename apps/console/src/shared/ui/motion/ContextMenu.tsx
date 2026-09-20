import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { overlayEnter, useReducedMotion } from '../../theme/motion'
import { menuCursorKeydown, useMenuCursor } from '../../data/useMenuCursor'
import { MenuRow } from '../Popover'
import { boundedMenuPoint, type MenuPoint } from './motionFamilyState'

export interface ContextMenuItem {
  icon?: ReactNode; label: string; hint?: string; onSelect: () => void; danger?: boolean; disabled?: boolean
}
export function ContextMenu({ items, children, disabled }: { items: ContextMenuItem[]; children: ReactNode; disabled?: boolean }) {
  const [placement, setPlacement] = useState<{ point: MenuPoint; sequence: number } | null>(null)
  const menu = useRef<HTMLDivElement>(null)
  const sequence = useRef(0)
  const pendingTouch = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reduced = useReducedMotion()
  const cursor = useMenuCursor({ containerRef: menu, count: items.length, openKey: placement ? String(placement.sequence) : null })
  const cancelTouch = useCallback(() => {
    if (pendingTouch.current !== null) clearTimeout(pendingTouch.current)
    pendingTouch.current = null
  }, [])
  const dismiss = useCallback(() => { setPlacement(null) }, [])
  const returnToInvoker = useCallback(() => { dismiss(); cursor.restoreFocus() }, [dismiss, cursor.restoreFocus])
  const open = (point: MenuPoint) => {
    cancelTouch()
    if (disabled || !items.length) return
    setPlacement({ sequence: ++sequence.current, point: boundedMenuPoint(point, { width: window.innerWidth, height: window.innerHeight }, { width: 220, height: Math.min(items.length * 40 + 12, 360) }) })
  }
  useEffect(() => cancelTouch, [cancelTouch])
  useEffect(() => {
    if (disabled || !items.length) { cancelTouch(); dismiss() }
  }, [disabled, items.length, cancelTouch, dismiss])
  useEffect(() => {
    if (!placement) return
    const pointer = (event: MouseEvent) => { if (!menu.current?.contains(event.target as Node)) dismiss() }
    const keyboard = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); returnToInvoker() }
      else menuCursorKeydown(event, { move: cursor.move, dismiss: returnToInvoker })
    }
    document.addEventListener('mousedown', pointer)
    document.addEventListener('keydown', keyboard)
    window.addEventListener('scroll', dismiss, true)
    return () => {
      document.removeEventListener('mousedown', pointer)
      document.removeEventListener('keydown', keyboard)
      window.removeEventListener('scroll', dismiss, true)
    }
  }, [placement, dismiss, returnToInvoker, cursor.move])
  const keyboardOpen = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ContextMenu' && !(event.key === 'F10' && event.shiftKey)) return
    const focused = document.activeElement instanceof HTMLElement ? document.activeElement : event.currentTarget
    const anchor = (focused.closest('[data-ctx-anchor]') ?? focused).getBoundingClientRect()
    event.preventDefault()
    event.stopPropagation()
    open({ x: Math.round(anchor.left + 24), y: Math.round(anchor.top + Math.min(anchor.height, 32)) })
  }
  const rows = items.map((item, index) => <div key={`${item.label}:${index}`} data-active={cursor.active === index || undefined} className={item.danger ? '[&_span]:!text-danger' : undefined}>
    <MenuRow role="menuitem" tabIndex={cursor.tabIndexFor(index)} disabled={item.disabled} icon={item.icon} label={item.label} hint={item.hint} selected={cursor.active === index}
      onClick={() => { if (!item.disabled) { item.onSelect(); returnToInvoker() } }} />
  </div>)
  const attributes = { ref: menu, role: 'menu', 'aria-orientation': 'vertical' as const,
    className: 'glass fixed z-[var(--z-menu)] min-w-[200px] max-h-[360px] overflow-y-auto rounded-xl border border-outline/30 p-1.5 shadow-lg',
    style: { left: placement?.point.x, top: placement?.point.y, transformOrigin: 'top left' } }
  return <>
    <div onKeyDown={keyboardOpen} onContextMenu={event => { event.preventDefault(); open({ x: event.clientX, y: event.clientY }) }}
      onTouchStart={event => {
        cancelTouch()
        const touch = event.touches[0]
        if (!touch || disabled || !items.length) return
        const point = { x: touch.clientX, y: touch.clientY }
        pendingTouch.current = setTimeout(() => open(point), 500)
      }} onTouchEnd={cancelTouch} onTouchMove={cancelTouch} onTouchCancel={cancelTouch}>{children}</div>
    {createPortal(reduced ? placement && <div {...attributes}>{rows}</div> : <AnimatePresence>{placement && <motion.div {...attributes} variants={overlayEnter} initial="initial" animate="animate" exit="exit">{rows}</motion.div>}</AnimatePresence>, document.body)}
  </>
}
