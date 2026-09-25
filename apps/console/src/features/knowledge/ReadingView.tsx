import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { Highlighter, PanelRight, Search, X, Zap } from 'lucide-react'
import { FindBar } from '../../shared/ui/FindBar'
import { Button } from '../../shared/ui/Button'
import { TextArea } from '../../shared/ui/forms'
import { Markdown } from '../../shared/ui/Markdown'
import { ProgressRing } from '../../shared/ui/ProgressRing'
import { SelectionPill } from '../../shared/ui/SelectionPill'
import { IconButton } from '../../shared/ui/IconButton'
import { InlineError } from '../../shared/ui/InlineError'
import { PROSE_MEASURE_CLASS } from '../../shared/theme/measure'
import { prefersReducedMotion } from '../../shared/theme/motion'
import { api, type KnowledgeAnnotation, type KnowledgeItem } from '../../shared/data/api'
import { anchorFromSelection, clearMarks, markAnchors, scrollProgress } from './readingAnchors'
import { getReadingPosition, setReadingPosition } from './readingPosition'
import { parseOutline, type OutlineEntry } from './readingOutline'
import { DocumentOutline } from './DocumentOutline'
import { RestructureControl } from './RestructureControl'
import { RsvpReader } from './RsvpReader'

const WPM = 220

export const RAIL_SPLIT_WIDTH = '58rem'

const READING_LINE = 0.25

function articleHeadings(article: HTMLElement): HTMLElement[] {
  return Array.from(article.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6'))
}

export function activeHeadingIndex(scroller: HTMLElement, headings: HTMLElement[]): number | null {
  if (headings.length === 0) return null
  const box = scroller.getBoundingClientRect()
  const line = box.top + box.height * READING_LINE
  let current: number | null = null
  headings.forEach((h, i) => { if (h.getBoundingClientRect().top <= line) current = i })
  return current
}

export function articleBlocks(article: HTMLElement): HTMLElement[] {
  const heading = article.querySelector<HTMLElement>('h1, h2, h3, h4, h5, h6')
  const parent = heading?.parentElement ?? (article.firstElementChild as HTMLElement | null) ?? article
  return (Array.from(parent.children) as HTMLElement[]).filter((el) => (el.textContent ?? '').trim())
}

export function headingForEntry(article: HTMLElement, entries: OutlineEntry[], offset: number): HTMLElement | null {
  const headings = articleHeadings(article)
  if (headings.length !== entries.length) return null
  const i = entries.findIndex((e) => e.offset === offset)
  return i < 0 ? null : headings[i] ?? null
}

function normalizeHeading(s: string): string {
  return s.replace(/^#+\s*/, '').replace(/[^\p{L}\p{N}\s]/gu, '').trim().replace(/\s+/g, ' ').toLowerCase()
}

export function bodyOpensWithTitle(content: string, title: string): boolean {
  const heading = content.split('\n').find((line) => line.trim())?.trim() ?? ''
  if (!/^#{1,6}\s/.test(heading)) return false
  const t = normalizeHeading(title)
  return !!t && normalizeHeading(heading) === t
}

export function ReadingView({
  item, annotations, onAnnotationsChanged, insightRail, onRestructured,
}: {
  item: KnowledgeItem
  annotations: KnowledgeAnnotation[]
  onAnnotationsChanged: () => void
  insightRail?: React.ReactNode
  onRestructured?: () => void
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const articleRef = useRef<HTMLDivElement | null>(null)
  const pillRef = useRef<HTMLButtonElement | null>(null)
  const composerRef = useRef<HTMLDivElement | null>(null)
  const [progress, setProgress] = useState(0)
  const [pending, setPending] = useState<{ quote: string; occurrence: number; x: number; y: number } | null>(null)
  const [composing, setComposing] = useState<{ quote: string; occurrence: number; x: number; y: number } | null>(null)
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const [unresolved, setUnresolved] = useState<string[]>([])
  const [railOpen, setRailOpen] = useState(false)
  const [activeOffset, setActiveOffset] = useState<number | null>(null)
  const [findOpen, setFindOpen] = useState(false)
  const [blockText, setBlockText] = useState<string[]>([])
  const [rsvpOpen, setRsvpOpen] = useState(false)

  const content = item.content || ''
  const minutes = item.word_count ? Math.max(1, Math.round(item.word_count / WPM)) : 0
  const titleIsInBody = bodyOpensWithTitle(content, item.title || item.url_title || '')
  const outline = useMemo(() => parseOutline(content), [content])
  const hasOutline = outline.some((e) => e.text)
  const railHasContent = hasOutline || !!insightRail
  const railName = [hasOutline ? 'Outline' : '', insightRail ? 'Insights' : ''].filter(Boolean).join(' & ')

  useEffect(() => {
    const root = scrollRef.current
    if (!root) return
    let frame = 0
    const read = () => {
      frame = 0
      setProgress(scrollProgress(root))
    }
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(read) }
    read()
    root.addEventListener('scroll', onScroll, { passive: true })
    return () => { root.removeEventListener('scroll', onScroll); if (frame) cancelAnimationFrame(frame) }
  }, [content])

  const restored = useRef(false)
  useEffect(() => {
    restored.current = false
    const root = scrollRef.current
    if (!root) return
    const saved = getReadingPosition(item.id)
    if (!saved) { restored.current = true; return }
    const frame = requestAnimationFrame(() => {
      const span = root.scrollHeight - root.clientHeight
      if (span > 0) root.scrollTop = saved.pct * span
      setProgress(scrollProgress(root))
      restored.current = true
    })
    return () => cancelAnimationFrame(frame)
  }, [item.id, content])

  useEffect(() => {
    if (!restored.current) return
    const t = setTimeout(() => setReadingPosition(item.id, progress), 400)
    return () => clearTimeout(t)
  }, [item.id, progress])

  const markedReading = useRef(false)
  useEffect(() => { markedReading.current = false }, [item.id])
  useEffect(() => {
    if (markedReading.current || progress < 0.02 || progress >= 0.98) return
    if ((item.read_state || 'unread') !== 'unread') return
    markedReading.current = true
    api.setKnowledgeReadState(item.id, 'reading').catch(() => { markedReading.current = false })
  }, [item.id, item.read_state, progress])

  useEffect(() => {
    const article = articleRef.current
    setBlockText(article ? articleBlocks(article).map((el) => el.textContent ?? '') : [])
  }, [content])

  const findSegments = useCallback((block: string) => [block], [])
  const findNode = useCallback((_block: string, i: number) => {
    const article = articleRef.current
    return article ? articleBlocks(article)[i] : null
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'f' || !(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
        && !t.closest('[role="search"]')) return
      e.preventDefault()
      setFindOpen((o) => !o)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    const root = scrollRef.current
    const article = articleRef.current
    if (!root || !article || outline.length === 0) return
    let frame = 0
    const read = () => {
      frame = 0
      const headings = articleHeadings(article)
      if (headings.length !== outline.length) { setActiveOffset(null); return }
      const i = activeHeadingIndex(root, headings)
      setActiveOffset(i === null ? null : outline[i].offset)
    }
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(read) }
    read()
    root.addEventListener('scroll', onScroll, { passive: true })
    return () => { root.removeEventListener('scroll', onScroll); if (frame) cancelAnimationFrame(frame) }
  }, [content, outline])

  useEffect(() => {
    const article = articleRef.current
    const root = scrollRef.current
    if (!article || !root) return
    const inOwnUi = (t: EventTarget | null) =>
      (pillRef.current && t instanceof Node && pillRef.current.contains(t)) ||
      (composerRef.current && t instanceof Node && composerRef.current.contains(t))

    const recompute = () => {
      const selection = article.ownerDocument.getSelection()
      const anchor = anchorFromSelection(article, selection)
      if (!anchor || !selection || selection.rangeCount === 0) { setPending(null); return }
      const rect = selection.getRangeAt(0).getBoundingClientRect()
      const box = root.getBoundingClientRect()
      setPending({
        ...anchor,
        x: rect.left - box.left + root.scrollLeft + rect.width / 2,
        y: rect.top - box.top + root.scrollTop - 8,
      })
    }

    let frame = 0
    const onSelectionChange = () => {
      if (frame) return
      frame = requestAnimationFrame(() => { frame = 0; recompute() })
    }
    const onUp = (e: MouseEvent) => { if (!inOwnUi(e.target)) recompute() }
    const onDown = (e: MouseEvent) => { if (!inOwnUi(e.target)) setPending(null) }
    document.addEventListener('mouseup', onUp)
    document.addEventListener('selectionchange', onSelectionChange)
    root.addEventListener('mousedown', onDown)
    return () => {
      document.removeEventListener('mouseup', onUp)
      document.removeEventListener('selectionchange', onSelectionChange)
      root.removeEventListener('mousedown', onDown)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [content])

  useLayoutEffect(() => {
    const article = articleRef.current
    if (!article) return
    setUnresolved(markAnchors(article, annotations))
    return () => clearMarks(article)
  }, [annotations, content, rsvpOpen])

  const article = useMemo(
    () => <Markdown className="reading">{content}</Markdown>,
    [content],
  )

  const openComposer = useCallback(() => {
    if (!pending) return
    setComposing(pending)
    setNote('')
    setPending(null)
    articleRef.current?.ownerDocument.getSelection()?.removeAllRanges()
  }, [pending])

  const closeComposer = useCallback(() => { setComposing(null); setNote('') }, [])

  const selectOutlineEntry = useCallback((entry: OutlineEntry) => {
    const article = articleRef.current
    if (!article) return
    headingForEntry(article, outline, entry.offset)?.scrollIntoView?.({
      block: 'start',
      behavior: prefersReducedMotion() ? 'auto' : 'smooth',
    })
  }, [outline])

  async function save() {
    if (!composing) return
    setSaving(true)
    setErr('')
    try {
      await api.createKnowledgeAnnotation(item.id, {
        quote: composing.quote,
        occurrence: composing.occurrence,
        note: note.trim() || undefined,
      })
      setComposing(null)
      setNote('')
      onAnnotationsChanged()
    } catch {
      setErr('Could not save that highlight.')
    } finally {
      setSaving(false)
    }
  }

  const pct = Math.round(progress * 100)
  const highlightHint = pending
    ? undefined
    : 'Select a passage in the article first'

  if (rsvpOpen) return <RsvpReader item={item} onClose={() => setRsvpOpen(false)} />

  return (
    <div className="@container flex h-full min-h-0 flex-col gap-m">
      {err && <InlineError onDismiss={() => setErr('')}>{err}</InlineError>}

      {
}
      <div className="flex shrink-0 items-center gap-s">
        <ProgressRing pct={progress} tone="var(--color-primary)" size={22} label={`Reading progress: ${pct}%`} />
        <span className="text-on-surface-low" data-type="label-s">
          {pct}% read{minutes ? ` · ${minutes} min` : ''}
          {annotations.length ? ` · ${annotations.length} highlight${annotations.length === 1 ? '' : 's'}` : ''}
        </span>
        <div className="ml-auto flex items-center gap-s">
          <Button size="sm" variant="ghost" onClick={() => setRsvpOpen(true)}>
            <Zap size={14} /> Rapid read
          </Button>
          {
}
          <Button size="sm" variant="ghost" ariaExpanded={findOpen}
            onClick={() => setFindOpen((o) => !o)}>
            <Search size={14} /> Find
          </Button>
          <Button size="sm" variant="ghost" onClick={openComposer}
            disabled={!pending} disabledReason={highlightHint}>
            <Highlighter size={14} /> Highlight selection
          </Button>
          {
}
          <RestructureControl item={item} selection={pending?.quote}
            onDone={() => { onRestructured?.(); onAnnotationsChanged() }} />
          {
}
          {railHasContent && (
            <div className="@min-[58rem]:hidden">
              <Button size="sm" variant="ghost" ariaExpanded={railOpen}
                onClick={() => setRailOpen((v) => !v)}>
                <PanelRight size={14} /> {railName}
              </Button>
            </div>
          )}
        </div>
      </div>

      {unresolved.length > 0 && (
        <p className="shrink-0 text-on-surface-low" data-type="body-s">
          {unresolved.length} highlight{unresolved.length === 1 ? '' : 's'} no longer match the text and
          {unresolved.length === 1 ? ' is' : ' are'} listed under More details rather than marked here.
        </p>
      )}

      {
}
      <div className="flex min-h-0 flex-1 flex-col gap-m @min-[58rem]:flex-row">
        {
}
        <div ref={scrollRef} tabIndex={0} role="group" aria-label="Article body"
          className="relative min-h-0 flex-1 overflow-y-auto rounded-lg border border-outline-variant/40 bg-surface-container">
          <AnimatePresence>
            {
}
            {findOpen && (
              <FindBar items={blockText} segmentsOf={findSegments} nodeOf={findNode}
                scrollRef={scrollRef} label="Find in article" onClose={() => setFindOpen(false)} />
            )}
          </AnimatePresence>
          {
}
          <div className={`mx-auto ${PROSE_MEASURE_CLASS} px-l py-xl`}>
            {
}
            {!titleIsInBody && (
              <h2 className="mb-xl text-on-surface" data-type="display-s">{item.title || item.url_title || 'Untitled'}</h2>
            )}
            <div ref={articleRef}>{article}</div>
          </div>

          {pending && (
            <SelectionPill ref={pillRef} icon={Highlighter} label="Highlight"
              x={pending.x} y={pending.y} onPress={openComposer} />
          )}

          {composing && (
            <div ref={composerRef}
              onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); closeComposer() } }}
              className="absolute z-40 w-[min(24rem,80vw)] -translate-x-1/2 rounded-xl bg-surface-highest p-m shadow-xl ring-1 ring-outline-variant/50"
              style={{ left: composing.x, top: composing.y }}>
              <div className="mb-2 max-h-16 overflow-y-auto rounded-md bg-surface-low px-2 py-1.5 text-on-surface-var italic line-clamp-3" data-type="body-s">
                “{composing.quote}”
              </div>
              <TextArea value={note} onChange={setNote} rows={3} size="sm" autoFocus
                ariaLabel="Note on this passage (optional)"
                placeholder="Why this matters… (optional)" />
              <div className="mt-2 flex justify-end gap-s">
                <Button size="sm" variant="ghost" onClick={closeComposer}>Cancel</Button>
                <Button size="sm" onClick={save} loading={saving}>Keep highlight</Button>
              </div>
            </div>
          )}
        </div>

        {
}
        {railHasContent && (
          <aside tabIndex={0} aria-label={`Article ${railName.toLowerCase()}`}
            className={`${railOpen ? 'flex' : 'hidden'} max-h-[16rem] min-h-0 shrink-0 flex-col gap-l overflow-y-auto rounded-lg border border-outline-variant/40 bg-surface-container p-m @min-[58rem]:flex @min-[58rem]:max-h-none @min-[58rem]:w-[19rem]`}>
            {
}
            <DocumentOutline entries={outline} activeOffset={activeOffset} onSelect={selectOutlineEntry} />
            {insightRail}
          </aside>
        )}
      </div>
    </div>
  )
}

export function AnnotationList({ annotations, onDelete }: {
  annotations: KnowledgeAnnotation[]
  onDelete: (id: string) => void
}) {
  return (
    <div className="flex flex-col gap-1.5">
      {annotations.map((a) => (
        <div key={a.id} className="rounded-md bg-surface-container px-m py-2">
          <div className="flex items-start gap-2">
            <blockquote className="min-w-0 flex-1 text-on-surface-var italic" data-type="body-s">“{a.quote}”</blockquote>
            {
}
            <IconButton icon={X} size={26} iconSize={14} onClick={() => onDelete(a.id)}
              label={`Remove highlight: ${a.quote.slice(0, 40)}`} title="Remove highlight" />
          </div>
          {a.note && <p className="mt-1 text-on-surface" data-type="body-s">{a.note}</p>}
        </div>
      ))}
    </div>
  )
}
