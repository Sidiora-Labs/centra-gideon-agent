import { useEffect, useRef, useState } from 'react'
import { captureFocus, returnFocus } from './focusNavigation'

export function useFocusReturn<T extends HTMLElement = HTMLDivElement>() {
  const element = useRef<T>(null)
  const [origin] = useState(captureFocus)
  useEffect(() => {
    const mounted = element.current
    return () => returnFocus(origin, mounted)
  }, [origin])
  return element
}
