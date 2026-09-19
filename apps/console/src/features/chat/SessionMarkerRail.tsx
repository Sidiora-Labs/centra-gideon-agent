import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type RefObject } from 'react'
import { ArrowDown } from 'lucide-react'
import { turnText, type ChatTurn } from './chatTypes'
import { clockTime, fullStamp, isoStamp } from '../../shared/data/epoch'

type ViewportPosition = {
  top: number
  height: number
}

type MarkerTurn = Pick<ChatTurn, 'role'> & Partial<Pick<ChatTurn, 'segments' | 'ts'>>

export interface SessionMarkerRailProps {
  turns: readonly MarkerTurn[]
  scrollRef: RefObject<HTMLDivElement | null>
  nodeOf: (index: number) => HTMLElement | null | undefined
  showReturnToNewest: boolean
  onReturnToNewest: () => void
}

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))

export function SessionMarkerRail({
  turns,
  scrollRef,
  nodeOf,
  showReturnToNewest,
  onReturnToNewest,
}: SessionMarkerRailProps) {
  const railRef = useRef<HTMLDivElement>(null)
  const [viewport, setViewport] = useState<ViewportPosition>({ top: 0, height: 100 })
  const [current, setCurrent] = useState(0)

  useEffect(() => {
    const scroller = scrollRef.current
    if (!scroller) return

    const measure = () => {
      const contentHeight = Math.max(scroller.scrollHeight, scroller.clientHeight, 1)
      const height = clamp((scroller.clientHeight / contentHeight) * 100, 0, 100)
      const top = clamp((scroller.scrollTop / contentHeight) * 100, 0, 100 - height)
      setViewport((previous) => previous.top === top && previous.height === height
        ? previous
        : { top, height })

      const midpoint = scroller.scrollTop + scroller.clientHeight / 2
      let nearest = 0
      for (let index = 0; index < turns.length; index++) {
        const node = nodeOf(index)
        if (node && node.offsetTop <= midpoint) nearest = index
      }
      setCurrent((previous) => previous === nearest ? previous : nearest)
    }

    measure()
    scroller.addEventListener('scroll', measure, { passive: true })
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    resize?.observe(scroller)
    window.addEventListener('resize', measure)
    return () => {
      scroller.removeEventListener('scroll', measure)
      resize?.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [nodeOf, scrollRef, turns.length])

  const jumpTo = useCallback((index: number) => {
    nodeOf(index)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [nodeOf])

  const moveWithKeyboard = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number | null = null
    if (event.key === 'ArrowDown') next = Math.min(turns.length - 1, index + 1)
    else if (event.key === 'ArrowUp') next = Math.max(0, index - 1)
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = turns.length - 1
    if (next === null) return

    event.preventDefault()
    const buttons = railRef.current?.querySelectorAll<HTMLButtonElement>('[data-session-marker]')
    buttons?.[next]?.focus()
    jumpTo(next)
  }

  if (!turns.length) return null

  return (
    <aside aria-label="Session map" className="pointer-events-none absolute inset-y-0 right-2 z-20 flex items-center">
      <div className="pointer-events-auto flex flex-col items-center gap-1.5">
        <div ref={railRef} role="region" aria-label="Session map messages"
          aria-describedby="session-map-keyboard-help"
          className="relative flex flex-col items-center rounded-xl border border-outline-variant/50 bg-surface/95 py-1 shadow-md backdrop-blur-md">
          <span id="session-map-keyboard-help" className="sr-only">
            Use the Up and Down arrow keys to jump between messages. Home jumps to the first message and End jumps to the newest.
          </span>
          <div aria-hidden="true" className="pointer-events-none absolute inset-y-2 left-1/2 w-px -translate-x-1/2 bg-outline-variant/60">
            <span data-testid="session-map-viewport" className="absolute -left-1.5 w-3 rounded-pill border border-primary/70 bg-primary/15"
              style={{ top: `${viewport.top}%`, height: `${viewport.height}%` }} />
          </div>
          {turns.map((turn, index) => {
            const speaker = turn.role === 'user' ? 'You' : 'Assistant'
            const previewId = `session-marker-preview-${index}`
            const excerpt = turn.segments
              ? turnText({ role: turn.role, segments: turn.segments, ts: turn.ts }).slice(0, 240)
              : ''
            return (
              <button key={index} type="button" data-session-marker
                aria-label={`Jump to message ${index + 1}, ${speaker}`}
                aria-describedby={previewId}
                aria-current={current === index ? 'location' : undefined}
                onClick={() => jumpTo(index)} onKeyDown={(event) => moveWithKeyboard(event, index)}
                className="group relative inline-flex size-8 items-center justify-center rounded-md outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1 focus-visible:ring-offset-surface">
                <span aria-hidden="true" className={`relative rounded-pill transition-all group-hover:scale-125 ${
                  turn.role === 'user' ? 'size-2 bg-primary' : 'size-1.5 bg-on-surface-low'
                 } ${current === index ? 'ring-2 ring-primary/35 ring-offset-2 ring-offset-surface' : ''}`} />
                <span id={previewId} role="tooltip"
                  className="pointer-events-none invisible absolute right-[calc(100%+0.5rem)] top-1/2 z-20 w-72 -translate-y-1/2 rounded-lg border border-outline-variant bg-surface-high p-3 text-left opacity-0 shadow-lg group-hover:visible group-hover:opacity-100 group-focus-visible:visible group-focus-visible:opacity-100">
                  <span className="flex justify-between gap-3" data-type="caption">
                    <span className="font-medium text-on-surface">{turn.role === 'user' ? 'User' : 'Assistant'}</span>
                    {turn.ts && <time dateTime={isoStamp(turn.ts)} title={fullStamp(turn.ts)}>{clockTime(turn.ts)}</time>}
                  </span>
                  {excerpt && <span className="mt-2 block text-xs text-on-surface-low">
                    <span className="font-medium text-on-surface">{turn.role === 'user' ? 'Request' : 'Response'}</span><br />{excerpt}
                  </span>}
                </span>
              </button>
            )
          })}
        </div>
        {showReturnToNewest && (
          <button type="button" aria-label="Return to newest message" onClick={onReturnToNewest}
            className="inline-flex size-11 items-center justify-center rounded-pill border border-outline-variant/50 bg-surface/95 text-on-surface-var shadow-md backdrop-blur-md transition-colors hover:bg-surface-high hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
            <ArrowDown size={16} aria-hidden="true" />
          </button>
        )}
      </div>
    </aside>
  )
}
