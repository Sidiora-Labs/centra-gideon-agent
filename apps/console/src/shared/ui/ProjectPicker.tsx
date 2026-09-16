import { useCallback, useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { FolderKanban, ChevronDown, Check, Plus } from 'lucide-react'
import { api, type ProjectItem } from '../data/api'
import { menuCursorKeydown, useMenuCursor } from '../data/useMenuCursor'
import { overlayEnter, physics } from '../theme/motion'

export function ProjectPicker({ value, onChange, disabled, emptyLabel, emptyHint, openSignal }: {
  value: string
  onChange: (projectId: string) => void
  disabled?: boolean
  emptyLabel?: string
  emptyHint?: string
  openSignal?: number
}) {
  const [open, setOpen] = useState(false)
  const [projects, setProjects] = useState<ProjectItem[] | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const selectable = projects?.filter((p) => p.status !== 'archived' || p.id === value) ?? null
  const optionCount = 1 + (selectable?.length ?? 0)
  const selectedIdx = value ? Math.max(0, 1 + (selectable?.findIndex((pr) => pr.id === value) ?? -1)) : 0
  const { move, restoreFocus, tabIndexFor } = useMenuCursor({
    containerRef: listRef, count: optionCount, openKey: open ? 'open' : null, initialIndex: selectedIdx,
  })
  const closeAndReturnFocus = useCallback(() => { setOpen(false); restoreFocus() }, [restoreFocus])
  const lastSignal = useRef(openSignal ?? 0)
  useEffect(() => {
    if (openSignal === undefined || openSignal === lastSignal.current) return
    lastSignal.current = openSignal
    if (openSignal > 0) setOpen(true)
  }, [openSignal])

  useEffect(() => {
    if (projects || (!open && !value)) return
    api.projects().then(setProjects).catch(() => setProjects([]))
  }, [open, value, projects])

  useEffect(() => {
    if (value && projects && !projects.some((p) => p.id === value)) onChange('')
  }, [value, projects, onChange])

  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(false); restoreFocus(); return }
      menuCursorKeydown(e, { move, dismiss: closeAndReturnFocus })
    }
    window.addEventListener('pointerdown', onDown)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('pointerdown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open, move, restoreFocus, closeAndReturnFocus])

  const current = value ? projects?.find((p) => p.id === value) : null
  const label = value ? (current?.name ?? 'Project') : (emptyLabel ?? 'New project')

  return (
    <div ref={ref} className="relative">
      {
}
      <button type="button" disabled={disabled} onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox" aria-expanded={open}
        aria-label={label === 'Project' ? 'Project' : `Project: ${label}`}
        title="Choose the project this work scopes under"
        data-type="body-s"
        className="inline-flex h-8 items-center gap-1.5 rounded-pill bg-surface-high px-2.5 text-on-surface-var transition-colors hover:bg-surface-highest disabled:opacity-50">
        <FolderKanban size={14} className="shrink-0 text-primary" />
        {
}
        <span className="hidden sm:inline max-w-[140px] truncate">{label}</span>
        { }
        <motion.span className="shrink-0 opacity-60" animate={{ rotate: open ? 180 : 0 }} transition={physics.snappy}>
          <ChevronDown size={13} />
        </motion.span>
      </button>
      <AnimatePresence>
      {open && (
        <motion.div ref={listRef} role="listbox" aria-orientation="vertical"
          aria-label="Project"
          variants={overlayEnter} initial="initial" animate="animate" exit="exit"
          style={{ transformOrigin: 'top left' }}
          className="absolute z-30 mt-1 max-h-[300px] w-[240px] overflow-y-auto rounded-lg border border-outline-variant/50 bg-surface-container p-1 shadow-menu">
          {
}
          <button type="button" role="option" aria-selected={!value} tabIndex={tabIndexFor(0)}
            onClick={() => { onChange(''); closeAndReturnFocus() }}
            data-type="body-s"
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-on-surface-var hover:bg-surface-high">
            <Plus size={14} className="shrink-0 text-on-surface-low" />
            <span className="min-w-0 flex-1 truncate">{emptyLabel ?? 'New project'} {(emptyHint ?? '(auto-named)') && <span className="text-on-surface-low/70">{emptyHint ?? '(auto-named)'}</span>}</span>
            {!value && <Check size={13} className="shrink-0 text-primary" />}
          </button>
          {selectable === null ? (
            <div data-type="caption" className="px-2 py-2 text-on-surface-low">Loading…</div>
          ) : selectable.length === 0 ? (
            <div data-type="caption" className="px-2 py-2 text-on-surface-low">No existing projects.</div>
          ) : (
            <>
              {
}
              <div data-type="caption" className="mt-1 border-t border-outline-variant/40 px-2 pt-1.5 pb-0.5 uppercase tracking-wide text-on-surface-low">Existing</div>
              {selectable.map((p, i) => (
                <button key={p.id} type="button" role="option" aria-selected={value === p.id}
                  tabIndex={tabIndexFor(i + 1)}
                  onClick={() => { onChange(p.id); closeAndReturnFocus() }}
                  data-type="body-s"
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-on-surface-var hover:bg-surface-high">
                  <FolderKanban size={14} className="shrink-0 text-on-surface-low" />
                  <span className="min-w-0 flex-1 truncate">{p.name}</span>
                  { }
                  {p.status === 'archived' && <span data-type="caption" className="shrink-0 text-on-surface-low/60">archived</span>}
                  {value === p.id && <Check size={13} className="shrink-0 text-primary" />}
                </button>
              ))}
            </>
          )}
        </motion.div>
      )}
      </AnimatePresence>
    </div>
  )
}
