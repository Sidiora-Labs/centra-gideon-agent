import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from 'react'

export const composerMenuClass = 'fixed z-[var(--z-menu)] overflow-y-auto rounded-xl border border-outline-variant/40 bg-surface/95 p-1.5 shadow-xl backdrop-blur-md'
export const composerOptionClass = 'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors'

export function composerPopupPlacement(rect: Pick<DOMRect, 'width' | 'left' | 'top' | 'bottom'>, viewport: { width: number; height: number }, maxHeight: number, aboveMinimum: number): CSSProperties {
  const width = Math.max(0, Math.min(Math.max(rect.width, 280), 460, viewport.width - 16))
  const left = Math.max(8, Math.min(rect.left, viewport.width - width - 8))
  const above = rect.top - 8
  return { width, left, ...(above < aboveMinimum
    ? { top: rect.bottom + 8, maxHeight: Math.max(0, Math.min(maxHeight, viewport.height - rect.bottom - 16)) }
    : { bottom: viewport.height - rect.top + 8, maxHeight: Math.min(maxHeight, above) }) }
}

let sequence = 0
const activeMenus = new Set<number>()
export function useComposerTypeahead({ open, anchorRef, count, identity, idPrefix, onSelect, onClose, onActiveIndex, height, above }: {
  open: boolean; anchorRef: RefObject<HTMLElement | null>; count: number; identity: string; idPrefix: string
  onSelect: (index: number) => void; onClose: () => void; onActiveIndex: (index: number | null) => void; height: number; above: number
}) {
  const menuRef = useRef<HTMLDivElement>(null)
  const [selection, setSelection] = useState({ identity, index: 0 })
  const cursor = Math.max(0, Math.min(count - 1, selection.identity === identity ? selection.index : 0))
  const [position, setPosition] = useState<CSSProperties>({})
  const latest = useRef({ count, cursor, identity, onSelect, onClose })
  latest.current = { count, cursor, identity, onSelect, onClose }
  const selectCursor = (index: number) => setSelection({ identity, index })

  useLayoutEffect(() => {
    if (!open || !anchorRef.current) return
    const place = () => {
      if (anchorRef.current) setPosition(composerPopupPlacement(anchorRef.current.getBoundingClientRect(),
        { width: window.innerWidth, height: window.innerHeight }, height, above))
    }
    place()
    window.addEventListener('resize', place)
    document.addEventListener('scroll', place, true)
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(place)
    resize?.observe(anchorRef.current)
    return () => { window.removeEventListener('resize', place); document.removeEventListener('scroll', place, true); resize?.disconnect() }
  }, [open, anchorRef, height, above])

  useEffect(() => {
    onActiveIndex(open && count > 0 ? cursor : null)
    if (open && count > 0) document.getElementById(`${idPrefix}-opt-${cursor}`)?.scrollIntoView?.({ block: 'nearest' })
  }, [open, count, cursor, idPrefix, onActiveIndex])

  useEffect(() => {
    if (!open) return
    const owner = ++sequence
    activeMenus.add(owner)
    const contains = (target: EventTarget | null) => target instanceof Node && (anchorRef.current?.contains(target) || menuRef.current?.contains(target))
    const keydown = (event: KeyboardEvent) => {
      if (owner !== Math.max(...activeMenus) || event.defaultPrevented || event.isComposing || event.keyCode === 229) return
      if (event.target !== document && !contains(event.target)) return
      const state = latest.current
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); state.onClose(); return }
      if (!state.count) return
      const step = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0
      if (step) {
        event.preventDefault(); event.stopPropagation()
        setSelection({ identity: state.identity, index: (state.cursor + step + state.count) % state.count })
      } else if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault(); event.stopPropagation(); state.onSelect(state.cursor)
      }
    }
    const outside = (event: Event) => { if (!contains(event.target)) latest.current.onClose() }
    document.addEventListener('keydown', keydown, true)
    document.addEventListener('mousedown', outside, true)
    document.addEventListener('focusin', outside, true)
    return () => {
      activeMenus.delete(owner)
      document.removeEventListener('keydown', keydown, true)
      document.removeEventListener('mousedown', outside, true)
      document.removeEventListener('focusin', outside, true)
    }
  }, [open, anchorRef])
  return { menuRef, cursor, selectCursor, position }
}
