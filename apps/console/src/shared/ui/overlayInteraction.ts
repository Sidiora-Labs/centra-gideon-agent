import { useEffect, useRef } from 'react'

let nextLayer = 0
const layers = new Map<number, number>()

export function useDismissKey(key: string, dismiss: () => void, priority: number) {
  const identity = useRef<number | null>(null)
  if (identity.current === null) identity.current = ++nextLayer
  const latest = useRef(dismiss)
  latest.current = dismiss
  useEffect(() => {
    const id = identity.current!
    layers.set(id, priority)
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== key || event.defaultPrevented) return
      for (const [other, rank] of layers) {
        if (rank > priority || (rank === priority && other > id)) return
      }
      event.preventDefault()
      event.stopImmediatePropagation()
      latest.current()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      layers.delete(id)
      window.removeEventListener('keydown', onKey)
    }
  }, [key, priority])
}

export function useDockReservation(docked: boolean) {
  useEffect(() => {
    if (!docked) return
    const style = document.documentElement.style
    const adjust = (amount: number) => {
      const count = Number(style.getPropertyValue('--rightpanel-open')) || 0
      style.setProperty('--rightpanel-open', String(Math.max(0, count + amount)))
    }
    adjust(1)
    return () => adjust(-1)
  }, [docked])
}
