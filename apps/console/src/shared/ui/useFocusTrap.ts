import { useEffect, useRef } from 'react'
import { captureFocus, FocusScope } from './focusNavigation'

export function useFocusTrap<T extends HTMLElement = HTMLDivElement>() {
  const element = useRef<T>(null)
  const scope = useRef<FocusScope | null>(null)
  if (!scope.current) scope.current = new FocusScope(captureFocus())
  useEffect(() => {
    if (element.current) return scope.current!.attach(element.current)
  }, [])
  return element
}
