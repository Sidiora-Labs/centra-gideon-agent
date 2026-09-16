import { useCallback, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { focusCandidates } from './focusNavigation'
import { listKeyDestination, recordRowHeight, RowGeometry, type RowWindow } from './windowGeometry'

export const WINDOWING_THRESHOLD = 64
export const DEFAULT_OVERSCAN = 8
export interface WindowedListRenderContext { windowed: boolean }
export interface WindowedListProps<T> {
  items: readonly T[]
  rowKey: (item: T, index: number) => string
  rowHeights: 'uniform' | 'variable'
  estimateRowHeight: number
  gap?: number
  noun: string
  findHint: string
  anchorKey?: string
  overscan?: number
  className?: string
  enableRowKeyboard?: boolean
  children: (item: T, index: number, ctx: WindowedListRenderContext) => ReactNode
}

function scrollParent(element: HTMLElement): HTMLElement | null {
  for (let parent = element.parentElement; parent; parent = parent.parentElement) {
    if (/^(auto|scroll|overlay)$/.test(getComputedStyle(parent).overflowY)) return parent
  }
  return null
}

function contentOrigin(list: HTMLElement, scroller: HTMLElement): number {
  return list.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop
}

export function WindowedList<T>({
  items, rowKey, rowHeights, estimateRowHeight, gap = 0, noun, findHint,
  anchorKey, overscan = DEFAULT_OVERSCAN, className, enableRowKeyboard = true, children,
}: WindowedListProps<T>) {
  const total = items.length
  const windowed = total > WINDOWING_THRESHOLD
  const hintId = useId()
  const root = useRef<HTMLDivElement>(null)
  const scroller = useRef<HTMLElement | null>(null)
  const mounted = useRef(new Map<string, HTMLDivElement>())
  const rowRefs = useRef(new Map<string, (element: HTMLDivElement | null) => void>())
  const measurements = useRef(new Map<string, number>())
  const observer = useRef<ResizeObserver | null>(null)
  const variable = useRef(rowHeights === 'variable')
  variable.current = rowHeights === 'variable'
  const focus = useRef({ active: null as string | null, parked: null as string | null, pending: null as string | null })
  const [revision, setRevision] = useState(0)
  const [range, setRange] = useState<RowWindow>({ start: 0, end: Math.min(total, WINDOWING_THRESHOLD) })
  const keys = useMemo(() => items.map(rowKey), [items, rowKey])
  const geometry = useMemo(() => new RowGeometry(keys, estimateRowHeight, gap,
    rowHeights === 'variable' ? measurements.current : new Map()), [keys, estimateRowHeight, gap, rowHeights, revision])

  const selectWindow = useCallback((next: RowWindow) => {
    setRange(current => current.start === next.start && current.end === next.end ? current : next)
  }, [])
  const recalculate = () => {
    if (!windowed || !root.current) return
    const viewport = scroller.current
    selectWindow(geometry.visible(
      viewport ? viewport.scrollTop - contentOrigin(root.current, viewport) : 0,
      viewport?.clientHeight ?? estimateRowHeight * Math.min(total, WINDOWING_THRESHOLD), overscan,
    ))
  }
  const refresh = useRef(recalculate)
  refresh.current = recalculate

  const focusRow = useCallback((key: string) => {
    const row = mounted.current.get(key)
    if (!row) return false
    ;(focusCandidates(row)[0] ?? row).focus()
    return true
  }, [])

  const reveal = useCallback((index: number) => {
    const key = keys[index]
    if (key === undefined) return
    focus.current.pending = key
    focus.current.parked = null
    const viewport = scroller.current
    if (windowed && viewport && root.current) {
      const origin = contentOrigin(root.current, viewport)
      const top = origin + geometry.offsets[index]
      const bottom = origin + geometry.rowBottom(index)
      if (top < viewport.scrollTop) viewport.scrollTop = top
      else if (bottom > viewport.scrollTop + viewport.clientHeight) viewport.scrollTop = bottom - viewport.clientHeight
    }
    if (windowed) selectWindow(geometry.around(index, viewport?.clientHeight ?? 0, overscan))
    if (focusRow(key)) focus.current.pending = null
  }, [keys, geometry, windowed, overscan, selectWindow, focusRow])

  function rowRef(key: string) {
    let callback = rowRefs.current.get(key)
    if (!callback) {
      callback = element => {
        const previous = mounted.current.get(key)
        if (previous) {
          observer.current?.unobserve(previous)
          if (!element && previous.contains(document.activeElement)) focus.current.parked = key
          mounted.current.delete(key)
        }
        if (!element) return
        mounted.current.set(key, element)
        observer.current?.observe(element)
        if (variable.current && recordRowHeight(measurements.current, key, element.offsetHeight)) setRevision(value => value + 1)
      }
      rowRefs.current.set(key, callback)
    }
    return callback
  }

  useLayoutEffect(() => {
    const element = root.current
    if (!element) return
    scroller.current = scrollParent(element)
    refresh.current()
    if (!windowed) return
    const viewport = scroller.current
    let frame: number | undefined
    const schedule = () => {
      if (frame !== undefined) return
      frame = requestAnimationFrame(() => { frame = undefined; refresh.current() })
    }
    viewport?.addEventListener('scroll', schedule, { passive: true })
    const resize = viewport && typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => refresh.current()) : null
    if (viewport) resize?.observe(viewport)
    return () => {
      if (frame !== undefined) cancelAnimationFrame(frame)
      viewport?.removeEventListener('scroll', schedule)
      resize?.disconnect()
    }
  }, [windowed])

  useLayoutEffect(() => {
    if (rowHeights !== 'variable') return
    let changed = false
    for (const [key, element] of mounted.current) changed = recordRowHeight(measurements.current, key, element.offsetHeight) || changed
    if (changed) setRevision(value => value + 1)
    if (typeof ResizeObserver === 'undefined') return
    const resize = new ResizeObserver(entries => {
      let dirty = false
      for (const entry of entries) {
        const element = entry.target as HTMLElement
        const key = element.dataset.rowKey
        if (key !== undefined && mounted.current.get(key) === element) {
          dirty = recordRowHeight(measurements.current, key, element.offsetHeight) || dirty
        }
      }
      if (dirty) setRevision(value => value + 1)
    })
    observer.current = resize
    mounted.current.forEach(element => resize.observe(element))
    return () => { resize.disconnect(); observer.current = null }
  }, [rowHeights])

  useLayoutEffect(() => { refresh.current() }, [geometry])
  useLayoutEffect(() => {
    const live = new Set(keys)
    for (const key of rowRefs.current.keys()) if (!live.has(key)) rowRefs.current.delete(key)
    for (const key of measurements.current.keys()) if (!live.has(key)) measurements.current.delete(key)
  }, [keys])
  useLayoutEffect(() => {
    const onFocus = (event: FocusEvent) => {
      const target = event.target as HTMLElement
      if (!root.current?.contains(target)) { focus.current.active = null; focus.current.parked = null; return }
      const row = target.closest<HTMLElement>('[data-row-key]')
      if (row?.parentElement === root.current) {
        focus.current.active = row.dataset.rowKey ?? null
        focus.current.parked = null
      }
    }
    document.addEventListener('focusin', onFocus)
    return () => document.removeEventListener('focusin', onFocus)
  }, [])
  useLayoutEffect(() => {
    const state = focus.current
    if (state.pending !== null && focusRow(state.pending)) { state.pending = null; return }
    if (!windowed || !root.current) return
    if (state.parked !== null) {
      if (mounted.current.has(state.parked)) {
        if (document.activeElement === root.current) focusRow(state.parked)
      } else if (document.activeElement === document.body) root.current.focus()
      return
    }
    if (state.active !== null && !mounted.current.has(state.active) && document.activeElement === document.body) {
      state.parked = state.active
      state.active = null
      root.current.focus()
    }
  })

  const consumedAnchor = useRef<string | undefined>(undefined)
  useLayoutEffect(() => {
    if (anchorKey === undefined) { consumedAnchor.current = undefined; return }
    if (anchorKey === consumedAnchor.current) return
    const index = geometry.positions.get(anchorKey)
    if (index !== undefined) { consumedAnchor.current = anchorKey; reveal(index) }
  }, [anchorKey, geometry, reveal])

  const start = windowed ? Math.min(range.start, Math.max(0, total - 1)) : 0
  const end = windowed ? Math.max(start + (total ? 1 : 0), Math.min(range.end, total)) : total
  return <>
    {windowed && <span id={hintId} className="sr-only">
      {`Showing ${end - start} of ${total} ${noun} at a time so the list stays fast. Browser find only searches what is on screen — ${findHint}`}
    </span>}
    <div ref={root} role="list" tabIndex={-1} className={className}
      aria-describedby={windowed ? hintId : undefined}
      style={windowed ? geometry.padding({ start, end }) : undefined}
      onKeyDown={event => {
        const target = event.target as HTMLElement
        if (!enableRowKeyboard || event.defaultPrevented || target.closest('input, textarea, select, [contenteditable="true"], [role="combobox"], [role="slider"]')) return
        if (target.closest('[role="list"]') !== root.current) return
        const row = target.closest<HTMLElement>('[data-row-key]')
        const key = row?.dataset.rowKey ?? focus.current.parked
        const current = key == null ? -1 : geometry.positions.get(key) ?? -1
        const page = Math.max(1, Math.floor((scroller.current?.clientHeight ?? estimateRowHeight * 10) / Math.max(1, estimateRowHeight)))
        const destination = listKeyDestination(event.key, current, total, page)
        if (destination === undefined) return
        event.preventDefault()
        reveal(destination)
      }}>
      {items.slice(start, end).map((item, offset) => {
        const index = start + offset
        return <div key={keys[index]} ref={rowRef(keys[index])} role="listitem" tabIndex={-1}
          data-row-key={keys[index]} aria-posinset={index + 1} aria-setsize={total}>
          {children(item, index, { windowed })}
        </div>
      })}
    </div>
  </>
}
