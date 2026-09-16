import type { CSSProperties } from 'react'
import { focusCandidates } from './focusNavigation'

export type PopupSide = 'top' | 'bottom'
export function popupSide(rect: DOMRect, preferred: PopupSide, height: number): PopupSide {
  const room = { top: rect.top, bottom: height - rect.bottom }
  const opposite = preferred === 'top' ? 'bottom' : 'top'
  return room[preferred] < 160 && room[opposite] > room[preferred] ? opposite : preferred
}

export function popupPosition(rect: DOMRect, side: PopupSide, align: 'left' | 'right', width: number): CSSProperties {
  const left = align === 'right' ? rect.right - width : rect.left
  return {
    left: Math.min(Math.max(8, left), Math.max(8, window.innerWidth - width - 8)),
    ...(side === 'bottom' ? { top: rect.bottom + 6 } : { bottom: window.innerHeight - rect.top + 6 }),
  }
}

export function keepPopupVisible(element: HTMLElement) {
  const rect = element.getBoundingClientRect()
  if (rect.top >= 8 && rect.bottom <= window.innerHeight - 8) return
  element.style.top = `${rect.top < 8 ? 8 : Math.max(8, window.innerHeight - 8 - rect.height)}px`
  element.style.bottom = 'auto'
}

export function ownsPopupKeyboard(element: HTMLElement | null) {
  const roles = new Set(['menu', 'listbox'])
  return !!element && [...element.querySelectorAll('[role]')].some((node) => roles.has(node.getAttribute('role') ?? ''))
}

export function enterPopup(element: HTMLElement | null) {
  if (!element || ownsPopupKeyboard(element)) return
  focusCandidates(element)[0]?.focus({ preventScroll: true })
}

const popupOrder = new Set<symbol>()
export function claimPopup() {
  const token = Symbol()
  popupOrder.add(token)
  return {
    current: () => [...popupOrder].at(-1) === token,
    release: () => { popupOrder.delete(token) },
  }
}
