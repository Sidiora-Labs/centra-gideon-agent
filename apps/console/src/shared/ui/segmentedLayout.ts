import { useLayoutEffect, useRef, useState } from 'react'

export function segmentNeedsMenu(naturalWidth: number, parentWidth: number, siblingWidths: number[]) {
  if (naturalWidth <= 0) return false
  const available = parentWidth - siblingWidths.reduce((sum, width) => sum + width, 0)
  return naturalWidth > available + 1
}

export function useSegmentLayout(enabled: boolean, options: unknown, iconOnly: boolean, compact: boolean) {
  const container = useRef<HTMLDivElement>(null)
  const probe = useRef<HTMLDivElement>(null)
  const [collapsed, setCollapsed] = useState(false)
  useLayoutEffect(() => {
    const element = container.current
    const parent = element?.parentElement
    if (!enabled || !element || !parent) { setCollapsed(false); return }
    const measure = () => {
      const naturalWidth = probe.current?.scrollWidth ?? 0
      if (naturalWidth <= 0) return
      const siblings = [...parent.children].filter((child) => child !== element)
        .map((child) => child.getBoundingClientRect().width)
      setCollapsed(segmentNeedsMenu(naturalWidth, parent.clientWidth, siblings))
    }
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    for (const target of [element, parent, probe.current]) if (target) observer?.observe(target)
    window.addEventListener('resize', measure)
    return () => { observer?.disconnect(); window.removeEventListener('resize', measure) }
  }, [enabled, options, iconOnly, compact])
  return { container, probe, collapsed }
}
