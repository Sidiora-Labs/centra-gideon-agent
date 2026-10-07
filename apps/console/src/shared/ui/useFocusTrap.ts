import { useEffect, useRef, type RefObject } from 'react'
import { captureFocus, FocusScope } from './focusNavigation'

interface FocusTrapOptions {
  enabled?: boolean
  initialFocus?: RefObject<HTMLElement | null>
  restoreFocus?: RefObject<HTMLElement | null>
}

export function useFocusTrap<T extends HTMLElement = HTMLDivElement>({ enabled = true, initialFocus, restoreFocus }: FocusTrapOptions = {}) {
  const element = useRef<T>(null)
  const origin = useRef<HTMLElement | null>(null)
  const wasEnabled = useRef(false)
  if (enabled && !wasEnabled.current) origin.current = captureFocus()
  wasEnabled.current = enabled
  useEffect(() => {
    const root = element.current
    if (!enabled || !root) return
    const scope = new FocusScope(restoreFocus?.current ?? origin.current)
    const initial = initialFocus?.current
    if (initial && root.contains(initial) && !initial.matches(':disabled') && !initial.closest('[hidden],[inert]')) initial.focus()
    return scope.attach(root)
  }, [enabled, initialFocus, restoreFocus])
  return element
}
