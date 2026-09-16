import { useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { ChevronUp, ChevronDown, X } from 'lucide-react'
import { spring } from '../theme/motion'
import { SearchField } from './SearchField'
import { IconButton } from './IconButton'
import { useFocusReturn } from './useFocusReturn'
import { useIsMobile } from '../../app/shell/useIsMobile'
import { findInText, matchingIndices } from './findText'

export function findAnnouncement(query: string, active: number, total: number): string {
  if (!query.trim()) return ''
  if (!total) return 'No matches'
  return `Match ${active + 1} of ${total}`
}

export function FindBar<T>({ items, segmentsOf, nodeOf, scrollRef, label, onClose }: {
  items: readonly T[]
  segmentsOf: (item: T) => string[]
  nodeOf: (item: T, index: number) => HTMLElement | null | undefined
  scrollRef: React.RefObject<HTMLElement | null>
  label: string
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [debounced, setDebounced] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const isMobile = useIsMobile()

  const focusReturnRef = useFocusReturn<HTMLDivElement>()

  useEffect(() => {
    const h = window.setTimeout(() => setDebounced(query), 150)
    return () => window.clearTimeout(h)
  }, [query])

  const matchIndices = useMemo(
    () => matchingIndices(items, segmentsOf, debounced), [items, segmentsOf, debounced])

  useEffect(() => { setActive(0) }, [debounced])

  useEffect(() => {
    const CSSns = (window as unknown as { CSS?: { highlights?: Map<string, unknown> } }).CSS
    const HighlightCtor = (window as unknown as { Highlight?: new (...r: Range[]) => unknown }).Highlight
    if (!CSSns?.highlights || !HighlightCtor) return
    const root = scrollRef.current
    const clear = () => { CSSns.highlights!.delete('gideon-find') }
    if (!root || !debounced.trim()) { clear(); return clear }
    const ranges: Range[] = []
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    let node: Node | null
    while ((node = walker.nextNode())) {
      const text = node.nodeValue ?? ''
      for (const m of findInText(text, debounced)) {
        const start = Math.min(m.start, text.length)
        const end = Math.min(m.end, text.length)
        if (end <= start) continue
        try {
          const r = document.createRange()
          r.setStart(node, start); r.setEnd(node, end)
          ranges.push(r)
        } catch {   }
      }
    }
    CSSns.highlights!.set('gideon-find', new HighlightCtor(...ranges))
    return clear
  }, [debounced, items, scrollRef])

  function go(delta: number) {
    if (!matchIndices.length) return
    const next = (active + delta + matchIndices.length) % matchIndices.length
    setActive(next)
    const i = matchIndices[next]
    nodeOf(items[i], i)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  return (
    <motion.div
      ref={focusReturnRef}
      initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
      transition={spring.spatialFast}
      className={`sticky top-2 z-30 flex items-center gap-1 rounded-pill border border-outline-variant/60 bg-surface/95 pl-1 pr-2 h-10 shadow-md backdrop-blur-md ${
        isMobile ? 'mx-l w-auto' : 'ml-auto mr-l w-fit'}`}
      role="search"
      onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose() } }}>
      <SearchField variant="inline" value={query} onChange={setQuery} inputRef={inputRef} autoFocus
        placeholder={label} ariaLabel={label} inlineIconSize={14}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); go(e.shiftKey ? -1 : 1) }
          else if (e.key === 'ArrowDown') { e.preventDefault(); go(1) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); go(-1) }
        }} />
      {
}
      <span aria-hidden="true" data-type="caption"
        className="min-w-[3.2rem] select-none text-center text-on-surface-low tabular-nums">
        {debounced.trim() ? (matchIndices.length ? `${active + 1}/${matchIndices.length}` : '0/0') : ''}
      </span>
      <div role="status" aria-live="polite" className="sr-only">
        {findAnnouncement(debounced, active, matchIndices.length)}
      </div>
      {
}
      <IconButton icon={ChevronUp} label="Previous match" onClick={() => go(-1)} disabled={!matchIndices.length}
        disabledReason={debounced.trim() ? 'Nothing matches this search yet' : 'Type something to search for'}
        size={28} iconSize={14} />
      <IconButton icon={ChevronDown} label="Next match" onClick={() => go(1)} disabled={!matchIndices.length}
        disabledReason={debounced.trim() ? 'Nothing matches this search yet' : 'Type something to search for'}
        size={28} iconSize={14} />
      <IconButton icon={X} label="Close find" onClick={onClose} size={28} iconSize={14} />
    </motion.div>
  )
}
