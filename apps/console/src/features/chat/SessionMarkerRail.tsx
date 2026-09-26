import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type RefObject } from 'react'
import { ArrowDown, Check, ListTree, Search, Wrench, X } from 'lucide-react'
import { turnText, type ChatTurn } from './chatTypes'
import { clockTime, fullStamp, isoStamp } from '../../shared/data/epoch'
import { useAppearance } from '../../app/shell/appearance'
import { TOKENS, type SelectToken } from '../../shared/theme/tokenRegistry'
import { findInText } from '../../shared/ui/findText'
import { sessionMapResults, turnLabel, type SessionMapResult } from './sessionMapSearch'

type ViewportPosition = {
  top: number
  height: number
}

type MarkerTurn = Pick<ChatTurn, 'role'> & Partial<Pick<ChatTurn, 'segments' | 'ts' | 'summary'>>
type MarkKind = 'tool' | 'error' | 'completion'

const MAP_DENSITY = TOKENS.find((token): token is SelectToken => token.kind === 'select' && token.varName === '--session-map-density')!

export interface SessionMarkerRailProps {
  turns: readonly MarkerTurn[]
  scrollRef: RefObject<HTMLDivElement | null>
  nodeOf: (index: number) => HTMLElement | null | undefined
  onJumpTo: (turnIndex: number) => void
  showReturnToNewest: boolean
  onReturnToNewest: () => void
  searchSource?: 'rail' | 'drawer' | 'transcript'
}

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))

export function SessionMarkerRail({
  turns,
  scrollRef,
  nodeOf,
  onJumpTo,
  showReturnToNewest,
  onReturnToNewest,
  searchSource = 'rail',
}: SessionMarkerRailProps) {
  const appearance = useAppearance()
  const compact = appearance.selectValue(MAP_DENSITY) === 'compact'
  const [viewport, setViewport] = useState<ViewportPosition>({ top: 0, height: 100 })
  const [current, setCurrent] = useState(0)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [query, setQuery] = useState('')
  const highlightedRoot = useRef<HTMLElement | null>(null)
  const highlightTimer = useRef<number | null>(null)

  const clearHighlight = useCallback(() => {
    const highlights = (window as unknown as { CSS?: { highlights?: Map<string, unknown> } }).CSS?.highlights
    highlights?.delete('gideon-session-map')
    highlightedRoot.current?.classList.remove('ring-2', 'ring-primary/30')
    highlightedRoot.current = null
    if (highlightTimer.current !== null) window.clearTimeout(highlightTimer.current)
  }, [])

  useEffect(() => clearHighlight, [clearHighlight])

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
    onJumpTo(index)
    setDrawerOpen(false)
  }, [onJumpTo])

  const selectResult = useCallback((index: number) => {
    const root = nodeOf(index)
    root?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    clearHighlight()
    if (root) {
      root.classList.add('ring-2', 'ring-primary/30')
      highlightedRoot.current = root
      highlightTimer.current = window.setTimeout(clearHighlight, 3000)
    }
    const CSSns = (window as unknown as { CSS?: { highlights?: Map<string, unknown> } }).CSS
    const HighlightCtor = (window as unknown as { Highlight?: new (...ranges: Range[]) => unknown }).Highlight
    if (!root || !CSSns?.highlights || !HighlightCtor) return
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    let node: Node | null
    while ((node = walker.nextNode())) {
      const match = findInText(node.nodeValue ?? '', query)[0]
      if (!match) continue
      const range = document.createRange()
      range.setStart(node, match.start)
      range.setEnd(node, match.end)
      CSSns.highlights.set('gideon-session-map', new HighlightCtor(range))
      break
    }
  }, [clearHighlight, nodeOf, query])

  const results = sessionMapResults(turns, query)

  const moveWithKeyboard = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number | null = null
    if (event.key === 'ArrowDown') next = Math.min(turns.length - 1, index + 1)
    else if (event.key === 'ArrowUp') next = Math.max(0, index - 1)
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = turns.length - 1
    if (next === null) return

    event.preventDefault()
    const buttons = event.currentTarget.closest('[data-session-map]')?.querySelectorAll<HTMLButtonElement>('[data-session-marker]')
    buttons?.[next]?.focus()
    onJumpTo(next)
  }

  if (!turns.length) return null

  return (
    <>
      <div className="absolute right-14 top-3 z-30 hidden md:block">
        <SearchControl open={searchOpen} query={query} source={searchSource} results={results}
          onToggle={() => { setSearchOpen((open) => !open); if (searchOpen) { setQuery(''); clearHighlight() } }}
          onQuery={(value) => { setQuery(value); clearHighlight() }} onSelect={selectResult} />
      </div>
      <aside aria-label="Session map" className="pointer-events-none absolute inset-y-0 right-2 z-20 hidden items-center md:flex">
        <div className={`pointer-events-auto flex max-h-[min(80vh,42rem)] flex-col items-center overflow-y-auto ${compact ? 'gap-1' : 'gap-1.5'}`}>
          <MapMarks id="desktop" turns={turns} viewport={viewport} current={current} compact={compact} jumpTo={jumpTo} moveWithKeyboard={moveWithKeyboard} />
          {showReturnToNewest && <ReturnToNewest onClick={onReturnToNewest} />}
        </div>
      </aside>
      <button type="button" aria-label="Open session map" aria-expanded={drawerOpen} onClick={() => setDrawerOpen(true)}
        className="absolute right-3 top-3 z-20 inline-flex size-11 items-center justify-center rounded-pill border border-outline-variant/50 bg-surface/95 text-on-surface-var shadow-md backdrop-blur-md md:hidden">
        <ListTree size={18} aria-hidden="true" />
      </button>
      {drawerOpen && (
        <div className="fixed inset-0 z-[var(--z-content)] md:hidden">
          <button type="button" aria-label="Close session map" onClick={() => setDrawerOpen(false)} className="absolute inset-0 bg-scrim/40" />
          <aside role="dialog" aria-modal="true" aria-label="Session map drawer"
            className="absolute inset-y-0 right-0 flex w-72 max-w-[85vw] flex-col border-l border-outline-variant bg-surface p-l shadow-xl">
            <header className="mb-l flex items-center justify-between gap-s">
              <h2 data-type="title-l">Session map</h2>
              <button type="button" aria-label="Close session map" onClick={() => setDrawerOpen(false)}
                className="inline-flex size-11 items-center justify-center rounded-pill text-on-surface-var hover:bg-surface-high">
                <X size={18} aria-hidden="true" />
              </button>
            </header>
            <SearchControl open query={query} source={searchSource} results={results}
              onToggle={() => { setQuery(''); clearHighlight() }}
              onQuery={(value) => { setQuery(value); clearHighlight() }} onSelect={selectResult} />
            <div className="flex min-h-0 flex-1 items-start justify-center overflow-y-auto">
              <MapMarks id="mobile" turns={turns} viewport={viewport} current={current} compact={compact} jumpTo={jumpTo} moveWithKeyboard={moveWithKeyboard} />
            </div>
            {showReturnToNewest && <div className="mt-l flex justify-center"><ReturnToNewest onClick={() => { onReturnToNewest(); setDrawerOpen(false) }} /></div>}
          </aside>
        </div>
      )}
    </>
  )
}

function marksFor(turn: MarkerTurn): MarkKind[] {
  const segments = turn.segments ?? []
  const marks: MarkKind[] = []
  if (segments.some((segment) => segment.kind === 'tool')) marks.push('tool')
  if (segments.some((segment) => segment.kind === 'error' || (segment.kind === 'tool' && (segment.ok === false || !!segment.agentError)))) marks.push('error')
  if (turn.role === 'assistant' && segments.some((segment) => segment.kind === 'text' || (segment.kind === 'tool' && segment.done))) marks.push('completion')
  return marks.length ? marks : ['completion']
}

function MapMarks({ id, turns, viewport, current, compact, jumpTo, moveWithKeyboard }: {
  id: string
  turns: readonly MarkerTurn[]
  viewport: ViewportPosition
  current: number
  compact: boolean
  jumpTo: (index: number) => void
  moveWithKeyboard: (event: KeyboardEvent<HTMLButtonElement>, index: number) => void
}) {
  return (
        <div data-session-map role="region" aria-label="Session map messages"
          data-session-map-state="open"
          aria-describedby={`${id}-session-map-keyboard-help`}
          className={`relative flex flex-col items-center rounded-xl border border-outline-variant/50 bg-rail py-1 shadow-md ${id === 'mobile' ? 'w-full' : ''}`}>
          <span role="status" aria-label="Session map position" aria-live="polite" aria-atomic="true" className="sr-only">
            {`Message ${current + 1} of ${turns.length}`}
          </span>
          <span id={`${id}-session-map-keyboard-help`} className="sr-only">
            Use the Up and Down arrow keys to jump between messages. Home jumps to the first message and End jumps to the newest.
          </span>
          <div aria-hidden="true" className={`pointer-events-none absolute inset-y-2 w-px bg-outline-variant/60 ${id === 'mobile' ? 'left-5' : 'left-1/2 -translate-x-1/2'}`}>
            <span data-testid="session-map-viewport" className="absolute -left-1.5 w-3 rounded-pill border border-primary/70 bg-primary/15"
              style={{ top: `${viewport.top}%`, height: `${viewport.height}%` }} />
          </div>
          {turns.map((turn, index) => {
            const speaker = turn.role === 'user' ? 'You' : 'Assistant'
            const previewId = `${id}-session-marker-preview-${index}`
            const marks = marksFor(turn)
            const label = turnLabel(turn)
            const excerpt = turn.summary || (turn.segments
              ? turnText({ role: turn.role, segments: turn.segments, ts: turn.ts }).slice(0, 240)
              : '')
            return (
              <button key={index} type="button" data-session-marker
                data-current={current === index}
                data-marker-tone={current === index ? 'current' : 'history'}
                aria-label={`Jump to message ${index + 1}, ${speaker}: ${label}`}
                aria-describedby={previewId}
                aria-current={current === index ? 'location' : undefined}
                onClick={() => jumpTo(index)} onKeyDown={(event) => moveWithKeyboard(event, index)}
                className={`group relative inline-flex items-center rounded-md outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1 focus-visible:ring-offset-rail ${id === 'mobile' ? 'w-full gap-s px-s py-xs text-left' : `justify-center ${compact ? 'size-6' : 'size-8'}`}`}>
                <span aria-hidden="true" className={`relative z-10 flex items-center gap-0.5 rounded-pill px-0.5 transition-transform group-hover:scale-125 ${current === index ? 'bg-primary ring-2 ring-primary ring-offset-2 ring-offset-rail' : 'bg-on-surface-low'}`}>
                  {marks.map((mark) => mark === 'tool'
                    ? <Wrench key={mark} data-mark="tool" size={8} className="text-primary" />
                    : mark === 'error'
                      ? <X key={mark} data-mark="error" size={9} className="text-danger" />
                      : <Check key={mark} data-mark="completion" size={8} className="text-on-surface-low" />)}
                </span>
                {id === 'mobile' && <span className="min-w-0 flex-1 truncate text-xs text-on-surface-var">{index + 1}. {label}</span>}
                <span id={previewId} role="tooltip"
                  className="pointer-events-none invisible absolute right-[calc(100%+0.5rem)] top-1/2 z-20 w-72 -translate-y-1/2 rounded-lg border border-outline-variant bg-surface-high p-3 text-left opacity-0 shadow-lg group-hover:visible group-hover:opacity-100 group-focus-visible:visible group-focus-visible:opacity-100">
                  <span className="flex justify-between gap-3" data-type="caption">
                    <span className="font-medium text-on-surface">{label}</span>
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
  )
}

function SearchControl({ open, query, source, results, onToggle, onQuery, onSelect }: {
  open: boolean
  query: string
  source: 'rail' | 'drawer' | 'transcript'
  results: SessionMapResult[]
  onToggle: () => void
  onQuery: (value: string) => void
  onSelect: (index: number) => void
}) {
  return <div className="relative">
    <button type="button" aria-label="Search session map" aria-expanded={open} onClick={onToggle}
      className="inline-flex size-8 items-center justify-center rounded-pill border border-outline-variant/50 bg-surface/95 text-on-surface-var shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
      {open ? <X size={14} aria-hidden="true" /> : <Search size={14} aria-hidden="true" />}
    </button>
    {open && <div role="search" aria-label="Search session map" data-search-origin={source} className="absolute right-10 top-0 w-72 rounded-xl border border-outline-variant bg-surface p-2 shadow-lg">
      <input type="search" autoFocus value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Search this session" aria-label="Search this session"
        className="h-9 w-full rounded-lg border border-outline-variant bg-surface-container px-3 text-sm text-on-surface outline-none focus:border-primary" />
      {query.trim() && <div className="mt-2 max-h-64 overflow-y-auto" role="list" aria-label="Session map search results">
        {results.length ? results.map(({ index, text, source }) => {
          const match = findInText(text, query)[0]
          return <button key={index} type="button" role="listitem" onClick={() => onSelect(index)} className="block w-full rounded-lg px-2 py-2 text-left hover:bg-surface-high">
            <span className="block truncate text-xs text-on-surface">{match ? <>{text.slice(Math.max(0, match.start - 60), match.start)}<mark className="rounded bg-primary/25 text-on-surface">{text.slice(match.start, match.end)}</mark>{text.slice(match.end, match.end + 80)}</> : text.slice(0, 140)}</span>
            <span className="mt-1 block text-[0.6875rem] text-on-surface-low">{source} · message {index + 1}</span>
          </button>
        }) : <p className="px-2 py-3 text-xs text-on-surface-low">No matches</p>}
      </div>}
    </div>}
  </div>
}

function ReturnToNewest({ onClick }: { onClick: () => void }) {
  return <button type="button" aria-label="Return to newest message" onClick={onClick}
    className="inline-flex size-11 items-center justify-center rounded-pill border border-outline-variant/50 bg-surface/95 text-on-surface-var shadow-md backdrop-blur-md transition-colors hover:bg-surface-high hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
    <ArrowDown size={16} aria-hidden="true" />
  </button>
}
