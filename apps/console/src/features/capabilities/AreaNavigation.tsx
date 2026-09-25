import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight, List, X, type LucideIcon } from 'lucide-react'
import { Button } from '../../shared/ui/Button'

export interface AreaDestination {
  id: string
  label: string
  icon: LucideIcon
  group?: string
}

export function AreaNavigation({ label, items, active, onChange, children }: {
  label: string
  items: readonly AreaDestination[]
  active: string
  onChange: (id: string) => void
  children: ReactNode
}) {
  const navigation = useRef<HTMLElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [edges, setEdges] = useState({ before: false, after: false })
  const measure = useCallback(() => {
    const strip = navigation.current
    if (!strip) return
    const first = strip.firstElementChild?.getBoundingClientRect()
    const last = strip.lastElementChild?.getBoundingClientRect()
    const bounds = strip.getBoundingClientRect()
    setEdges({ before: !!first && first.left < bounds.left - 1, after: !!last && last.right > bounds.right + 1 })
  }, [])
  const revealSelected = useCallback(() => {
    const strip = navigation.current
    const selected = strip?.querySelector('[aria-pressed="true"]')
    if (!strip || !selected || strip.scrollWidth <= strip.clientWidth) return
    const bounds = strip.getBoundingClientRect()
    const target = selected.getBoundingClientRect()
    const delta = target.left < bounds.left ? target.left - bounds.left
      : Math.max(0, target.right - bounds.right)
    if (delta) strip.scrollBy?.({ left: delta })
  }, [])
  useEffect(() => {
    measure()
    const strip = navigation.current
    if (!strip || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => { measure(); revealSelected() })
    observer.observe(strip)
    return () => observer.disconnect()
  }, [measure, revealSelected, items, expanded])
  useEffect(revealSelected, [active, expanded, revealSelected])
  const scroll = (direction: number) => {
    const strip = navigation.current
    strip?.scrollBy?.({ left: direction * strip.clientWidth * 0.75 })
  }
  return <div className="capability-area" data-navigation-expanded={expanded || undefined}>
    <div className="capability-area-navigation-shell">
      <Button variant="ghost" className="capability-area-menu" ariaLabel={(expanded ? 'Close sections' : 'All sections')} title={(expanded ? 'Close sections' : 'All sections')} ariaExpanded={expanded} onClick={() => setExpanded(value => !value)}>
        {expanded ? <X size={18} aria-hidden="true" /> : <List size={18} aria-hidden="true" />}
      </Button>
      {!expanded && (edges.before || edges.after) && <Button disabled={!edges.before} variant="ghost" className="capability-area-scroll" ariaLabel={'Previous sections'} onClick={() => scroll(-1)}><ChevronLeft size={18} aria-hidden="true" /></Button>}
    <nav ref={navigation} className="capability-area-navigation" aria-label={label} onScroll={measure}
      onFocusCapture={event => {
        const strip = navigation.current
        if (!strip || strip.scrollWidth <= strip.clientWidth) return
        const bounds = strip.getBoundingClientRect()
        const target = event.target.getBoundingClientRect()
        const delta = target.left < bounds.left ? target.left - bounds.left : Math.max(0, target.right - bounds.right)
        if (delta) strip.scrollBy?.({ left: delta })
      }}>
      {items.map((item, index) => {
        const Icon = item.icon
        return <div key={item.id} className="capability-area-destination">
          {item.group && items[index - 1]?.group !== item.group &&
            <p className="capability-area-group" data-type="label-s">{item.group}</p>}
          <Button variant={active === item.id ? 'tonal' : 'ghost'} shape="squircle"
            className="capability-area-link" ariaPressed={active === item.id}
            ariaLabel={item.label} title={item.label} onClick={() => { onChange(item.id); setExpanded(false) }}>
            <Icon size={18} aria-hidden="true" />
            <span className="capability-area-label">{item.label}</span>
          </Button>
        </div>
      })}
    </nav>
      {!expanded && (edges.before || edges.after) && <Button disabled={!edges.after} variant="ghost" className="capability-area-scroll" ariaLabel={'More sections'} onClick={() => scroll(1)}><ChevronRight size={18} aria-hidden="true" /></Button>}
    </div>
    <div className="capability-area-page">{children}</div>
  </div>
}
