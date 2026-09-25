import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Bookmark, FastForward, Pause, Play, Rewind, RotateCcw, X } from 'lucide-react'
import { api, type KnowledgeItem, type KnowledgeRsvpState } from '../../shared/data/api'
import { Button } from '../../shared/ui/Button'
import { IconButton } from '../../shared/ui/IconButton'
import { InlineError } from '../../shared/ui/InlineError'
import { ListSkeleton } from '../../shared/ui/ListScaffold'

export type RsvpWord = { text: string; start: number; end: number }
const punctuationOnly = /^[\p{P}\p{S}]+$/u
const openingOnly = /^[\p{Ps}\p{Pi}'"¿¡]+$/u

export function rsvpWords(text = ''): RsvpWord[] {
  const result: RsvpWord[] = []
  let prefix = ''
  let prefixStart: number | null = null
  for (const match of text.matchAll(/\S+/gu)) {
    const value = match[0]
    const start = match.index
    if (punctuationOnly.test(value)) {
      if (openingOnly.test(value) || result.length === 0) {
        prefix += value
        if (prefixStart === null) prefixStart = start
      } else {
        result[result.length - 1].text += value
        result[result.length - 1].end = start + value.length
      }
      continue
    }
    result.push({ text: prefix + value, start: prefixStart ?? start, end: start + value.length })
    prefix = ''
    prefixStart = null
  }
  if (prefix && result.length) result[result.length - 1].text += prefix
  return result
}

export function rsvpDelay(text: string, wpm: number, wordCount: number) {
  let multiplier = /[.!?…。！？](?:["'”’»)\]}]+)?$/u.test(text) ? 1.8
    : /[,;:](?:["'”’»)\]}]+)?$/u.test(text) ? 1.3
      : text.length > 8 ? 1.15 : 1
  return (60000 / Math.max(100, wpm)) * wordCount * multiplier
}

export function chunkAt(words: RsvpWord[], index: number, size: 1 | 2) {
  const selected = words.slice(index, index + size)
  const joined = selected.map(word => word.text).join(' ')
  if (size === 2 && selected.length === 2 && joined.length <= 20) return { words: selected, text: joined, count: 2 }
  return { words: selected.slice(0, 1), text: selected[0]?.text || '', count: selected.length ? 1 : 0 }
}

export function focalIndex(word: string) {
  const content = Array.from(word).map((char, index) => /[\p{L}\p{N}]/u.test(char) ? index : -1).filter(index => index >= 0)
  const length = content.length
  if (!length) return 0
  const index = length <= 1 ? 0 : length <= 5 ? 1 : length <= 9 ? 2 : length <= 13 ? 3 : 4
  return content[Math.min(index, length - 1)]
}

function FocalWord({ value }: { value: string }) {
  const chars = Array.from(value)
  const index = focalIndex(value)
  return <span className="flex w-full items-baseline justify-center font-mono text-3xl tracking-wide @min-[40rem]:text-5xl">
    <span className="flex-1 text-right whitespace-pre">{chars.slice(0, index).join('')}</span>
    <span className="text-primary">{chars[index] || ''}</span>
    <span className="flex-1 text-left whitespace-pre">{chars.slice(index + 1).join('')}</span>
  </span>
}

export function RsvpReader({ item, onClose }: { item: KnowledgeItem; onClose: () => void }) {
  const words = useMemo(() => rsvpWords(item.content || ''), [item.content])
  const [state, setState] = useState<KnowledgeRsvpState | null>(null)
  const [playing, setPlaying] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null)
  const current = useRef<KnowledgeRsvpState | null>(null)
  const generation = useRef(0)
  const saveQueue = useRef<Promise<KnowledgeRsvpState | undefined>>(Promise.resolve(undefined))

  useEffect(() => {
    let alive = true
    api.knowledgeRsvp(item.id).then(value => { if (alive) { current.current = value; setState(value) } })
      .catch(reason => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not open rapid reader.') })
    return () => { alive = false }
  }, [item.id])

  const enqueueSave = useCallback((next: KnowledgeRsvpState, version: number) => {
    const request = {
      word_index: next.word_index,
      wpm: next.wpm,
      chunk_size: next.chunk_size,
      content_revision: next.content_revision,
    }
    const run = saveQueue.current.catch(() => undefined).then(() => api.saveKnowledgeRsvp(item.id, request))
    saveQueue.current = run
    void run.then(value => {
      if (generation.current !== version) return
      current.current = value
      setState(value)
    }).catch(reason => {
      if (generation.current !== version) return
      setPlaying(false)
      setError(reason instanceof Error ? reason.message : 'Could not save reading position.')
    })
    return run
  }, [item.id])

  const persist = useCallback((next: KnowledgeRsvpState) => {
    const version = generation.current + 1
    generation.current = version
    current.current = next
    setState(next)
    if (pending.current) clearTimeout(pending.current)
    pending.current = setTimeout(() => {
      pending.current = null
      if (generation.current === version) enqueueSave(next, version)
    }, 120)
  }, [enqueueSave])

  const flush = useCallback(async (snapshot?: KnowledgeRsvpState | null) => {
    const latest = snapshot ?? current.current
    if (!latest) return undefined
    if (pending.current) { clearTimeout(pending.current); pending.current = null }
    return enqueueSave(latest, generation.current)
  }, [enqueueSave])

  useEffect(() => () => { if (pending.current) clearTimeout(pending.current) }, [])
  const chunk = state ? chunkAt(words, state.word_index, state.chunk_size) : { words: [], text: '', count: 0 }

  useEffect(() => {
    if (!playing || !state || !chunk.count) return
    const timeout = setTimeout(() => {
      const next = state.word_index + chunk.count
      if (next >= words.length) { setPlaying(false); return }
      persist({ ...state, word_index: next })
    }, rsvpDelay(chunk.text, state.wpm, chunk.count))
    return () => clearTimeout(timeout)
  }, [playing, state, chunk.count, chunk.text, words.length, persist])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)) return
      if (event.key === ' ') {
        event.preventDefault()
        if (playing) { setPlaying(false); void flush() } else setPlaying(true)
      }
      else if (event.key === 'ArrowLeft' && state) { event.preventDefault(); setPlaying(false); persist({ ...state, word_index: Math.max(0, state.word_index - 1) }) }
      else if (event.key === 'ArrowRight' && state) { event.preventDefault(); setPlaying(false); persist({ ...state, word_index: Math.min(words.length - 1, state.word_index + 1) }) }
      else if (event.key === 'Escape') { setPlaying(false); void flush().catch(() => undefined).finally(onClose) }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [flush, onClose, persist, playing, state, words.length])

  if (error && !state) return <div className="flex flex-col gap-m"><InlineError>{error}</InlineError><Button onClick={onClose}>Return to article</Button></div>
  if (!state) return <ListSkeleton rows={3} what="rapid reader" />
  const shownEnd = Math.min(words.length, state.word_index + chunk.count)
  const progress = words.length ? shownEnd / words.length : 0

  const update = (change: Partial<KnowledgeRsvpState>) => persist({ ...state, ...change })
  const pause = async () => { setPlaying(false); try { await flush() } catch { /* surfaced by enqueueSave */ } }
  const close = async () => { setPlaying(false); try { await flush() } catch { /* surfaced by enqueueSave */ } onClose() }
  const bookmark = async () => {
    const bookmarked = current.current
    if (!bookmarked) return
    try {
      await flush(bookmarked)
      const version = generation.current + 1
      generation.current = version
      const value = await api.bookmarkKnowledgeRsvp(item.id, bookmarked.word_index, bookmarked.content_revision)
      if (generation.current === version) { current.current = value; setState(value) }
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save bookmark.') }
  }
  const restore = async () => {
    setPlaying(false)
    try {
      await flush()
      const version = generation.current + 1
      generation.current = version
      const value = await api.restoreKnowledgeRsvp(item.id)
      if (generation.current === version) { current.current = value; setState(value) }
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not restore bookmark.') }
  }

  return <section aria-label="Rapid reader" className="@container flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-outline-variant/40 bg-surface-container">
    <header className="flex items-center gap-s border-b border-outline-variant/30 px-m py-s">
      <div className="min-w-0 flex-1"><p data-type="label-m" className="truncate text-on-surface">{state.title}</p><p data-type="caption" className="text-on-surface-low">Rapid reader · Space pauses or resumes</p></div>
      <IconButton icon={X} label="Return to article" onClick={() => { void close() }} />
    </header>
    {error && <InlineError onDismiss={() => setError('')}>{error}</InlineError>}
    {state.content_changed && <p role="status" data-type="body-s" className="px-m pt-m text-warning">The article changed. Your position was kept within the current text.</p>}
    <div className="relative flex min-h-[15rem] flex-1 items-center justify-center overflow-hidden bg-surface px-l">
      <div aria-hidden className="absolute inset-y-0 left-1/2 w-px bg-outline-variant/40" />
      <FocalWord value={chunk.text} />
    </div>
    <div className="h-1 bg-surface-high"><div className="h-full bg-primary" style={{ width: `${progress * 100}%` }} /></div>
    <div className="flex flex-col gap-m p-m">
      <div className="flex flex-wrap items-center gap-s">
        <IconButton icon={Rewind} label="Back 5 words" onClick={() => { setPlaying(false); update({ word_index: Math.max(0, state.word_index - 5) }) }} />
        <IconButton icon={playing ? Pause : Play} label={playing ? 'Pause' : 'Play'} onClick={() => { if (playing) void pause(); else setPlaying(true) }} />
        <IconButton icon={FastForward} label="Forward 5 words" onClick={() => { setPlaying(false); update({ word_index: Math.min(words.length - 1, state.word_index + 5) }) }} />
        <IconButton icon={RotateCcw} label="Restart" onClick={() => { setPlaying(false); update({ word_index: 0 }) }} />
        <IconButton icon={Bookmark} label="Save RSVP bookmark" onClick={() => { void bookmark() }} />
        <Button size="sm" variant="ghost" onClick={() => { void restore() }} disabled={state.bookmark_index === null} disabledReason="No RSVP bookmark yet">Restore bookmark</Button>
        <span className="ml-auto text-on-surface-low" data-type="label-s">{state.word_index + 1}{chunk.count === 2 ? `–${shownEnd}` : ''} / {words.length}</span>
      </div>
      <div className="flex flex-wrap items-center gap-l">
        <label className="flex items-center gap-s text-on-surface-var" data-type="label-s">Reading speed
          <input aria-label="Reading speed" type="range" min="100" max="1000" step="25" value={state.wpm} onChange={event => update({ wpm: Number(event.target.value) })} />
          <span className="w-12 text-right tabular-nums">{state.wpm}</span>
        </label>
        <div role="group" aria-label="Words per chunk" className="flex overflow-hidden rounded-md border border-outline-variant/40">
          {[1, 2].map(size => <button key={size} type="button" aria-pressed={state.chunk_size === size} onClick={() => update({ chunk_size: size as 1 | 2 })}
            className={`px-m py-2 ${state.chunk_size === size ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-var'}`}>{size} word{size === 1 ? '' : 's'}</button>)}
        </div>
      </div>
    </div>
  </section>
}
