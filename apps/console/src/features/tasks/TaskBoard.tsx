import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { AnimatePresence, motion, LayoutGroup } from 'framer-motion'
import { MessageSquare, GripVertical } from 'lucide-react'
import type { TaskItem } from '../../shared/data/api'
import { STATUSES, signalPriority, dueMeta, exitDoneCount } from './taskMeta'
import { prereqIds } from './dag'
import { spring, physics, expr, useReducedMotion } from '../../shared/theme/motion'
import { CollapseColumnButton, CollapsedBoardColumn, boardGridTemplate, useBoardCollapse } from '../../shared/ui/BoardCollapse'

export function TaskBoard({ tasks, onOpen, onMove }: { tasks: TaskItem[]; onOpen: (id: string) => void; onMove: (id: string, status: string) => void }) {
  const [dragId, setDragId] = useState<string | null>(null)
  const [overCol, setOverCol] = useState<string | null>(null)
  const active = useRef<string | null>(null)
  const frame = useRef<number | null>(null)
  const collapse = useBoardCollapse('board-collapsed:tasks')
  const reduced = useReducedMotion()
  const columns = useMemo(() => {
    const groups = new Map(STATUSES.map(status => [status.key, [] as TaskItem[]]))
    for (const task of tasks) groups.get(task.status)?.push(task)
    return STATUSES.map(s => ({ s, items: groups.get(s.key)! }))
  }, [tasks])
  useEffect(() => () => { if (frame.current !== null) cancelAnimationFrame(frame.current) }, [])
  const finish = () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current)
    frame.current = null; active.current = null; setDragId(null); setOverCol(null)
  }
  const start = (event: DragEvent, id: string) => {
    if (frame.current !== null) cancelAnimationFrame(frame.current)
    event.dataTransfer.setData('text/plain', id); event.dataTransfer.effectAllowed = 'move'
    active.current = id
    frame.current = requestAnimationFrame(() => { frame.current = null; if (active.current === id) setDragId(id) })
  }
  return <LayoutGroup><div className="grid h-full gap-m overflow-x-auto" style={{ gridTemplateColumns: boardGridTemplate(columns.map(({ s, items }) => collapse.isCollapsed(s.key, items.length))), gridAutoRows: 'minmax(180px, 1fr)' }}>
    {columns.map(({ s, items }) => {
      const isOver = overCol === s.key && dragId != null
      const dropStyle = {
        background: isOver ? `color-mix(in srgb, ${s.tone} 12%, var(--color-surface-container))` : 'color-mix(in srgb, var(--color-surface-container) 40%, transparent)',
        outline: isOver ? `1.5px dashed ${s.tone}` : '1.5px solid transparent',
      }
      const dropHandlers = {
        onDragOver: (event: DragEvent) => { if (active.current) { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; setOverCol(s.key) } },
        onDragLeave: (event: DragEvent) => { if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setOverCol(current => current === s.key ? null : current) },
        onDrop: (event: DragEvent) => {
          event.preventDefault()
          const id = event.dataTransfer.getData('text/plain') || active.current
          finish()
          if (id && tasks.some(task => task.id === id && task.provider !== 'project')) onMove(id, s.key)
        },
      }
      if (collapse.isCollapsed(s.key, items.length)) return <CollapsedBoardColumn key={s.key} icon={s.icon} label={s.label} count={items.length} tone={s.tone} {...dropHandlers} onExpand={() => collapse.toggle(s.key, items.length)} style={dropStyle} />
      return <motion.div key={s.key} animate={{ scale: isOver ? 1 + expr(reduced ? 0 : 0.012, reduced ? 0 : 0.3) : 1 }} transition={physics.snappy} {...dropHandlers} style={dropStyle} className="flex min-h-0 flex-col rounded-lg border border-outline-variant/25 p-s">
        <header className="mb-s flex shrink-0 items-center gap-s border-b border-outline-variant/25 px-1 py-s"><s.icon size={15} style={{ color: s.tone }} /><span data-type="label-s" className="font-medium text-on-surface">{s.label}</span><motion.span key={items.length} initial={reduced ? false : { scale: 0.5, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={physics.playful} data-type="caption" className="flex-1 text-on-surface-low tabular-nums">{items.length}</motion.span><CollapseColumnButton onCollapse={() => collapse.toggle(s.key, items.length)} /></header>
        <div className="flex min-h-0 flex-1 flex-col gap-s overflow-y-auto pr-0.5" tabIndex={0} role="group" aria-label={`${s.label} — ${items.length} task${items.length === 1 ? '' : 's'}`}>
          {items.length ? <AnimatePresence initial={false}>{items.map(t => <BoardCard key={t.id} t={t} tone={s.tone} onOpen={() => onOpen(t.id)} onDragStart={event => start(event, t.id)} onDragEnd={finish} dragging={dragId === t.id} />)}</AnimatePresence> : <div data-type="caption" className="flex flex-1 items-center justify-center rounded-md border border-dashed border-outline-variant/30 py-6 text-on-surface-low">{isOver ? 'Drop here' : '—'}</div>}
        </div>
      </motion.div>
    })}
  </div></LayoutGroup>
}

function BoardCard({ t, tone, onOpen, onDragStart, onDragEnd, dragging }: { t: TaskItem; tone: string; onOpen: () => void; onDragStart: (event: DragEvent) => void; onDragEnd: () => void; dragging: boolean }) {
  const pm = signalPriority(t.priority), due = dueMeta(t.due)
  const criteria = t.exit_criteria ?? []
  const dependencies = prereqIds(t).length
  const readOnly = t.provider === 'project'
  const reduced = useReducedMotion()
  const chips = [pm, due].filter((chip): chip is { label: string; tone: string } => !!chip)
  return <div draggable={!readOnly} onDragStart={onDragStart} onDragEnd={onDragEnd} onClick={onOpen} role="button" tabIndex={0} aria-label={t.title}
    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen() } }} className={`shrink-0 select-none rounded-lg focus-visible:outline-2 focus-visible:outline-primary ${readOnly ? 'cursor-pointer' : 'cursor-grab active:cursor-grabbing'}`}>
    <motion.div layout={!reduced} layoutId={`board-card-${t.id}`} initial={reduced ? false : { opacity: 0, scale: 0.94 }} animate={{
      opacity: dragging ? 0.5 : 1,
      scale: dragging ? 1 + expr(reduced ? 0 : 0.04, reduced ? 0 : 0.3) : 1,
      rotate: dragging ? -expr(reduced ? 0 : 2, 0) : 0,
      boxShadow: dragging ? 'var(--shadow-lift)' : 'var(--shadow-rest)',
    }} exit={{ opacity: 0, scale: reduced ? 1 : 0.9, transition: spring.spatialFast }} transition={spring.spatialDefault} whileHover={readOnly || reduced ? undefined : { y: -expr(2, 0.3) }} className="group relative overflow-hidden rounded-lg border border-outline-variant/25 bg-surface-container px-m py-m transition-colors hover:bg-surface-high">
      <span className="absolute inset-y-0 left-0 w-[3px]" style={{ background: tone }} />
      {!readOnly && <GripVertical size={13} className="pointer-events-none absolute right-1.5 top-2 text-on-surface-low opacity-0 transition-opacity group-hover:opacity-100" />}
      <div className="pl-1.5"><div data-type="label-s" title={t.title} className="line-clamp-2 pr-4 font-medium leading-snug text-on-surface">{t.title}</div>
        <div className="mt-s flex flex-wrap items-center gap-1.5">{chips.map((chip, index) => <span key={index} data-type="caption" className="rounded-md px-2 py-0.5" style={{ background: `color-mix(in srgb, ${chip.tone} 15%, transparent)`, color: chip.tone }}>{chip.label}</span>)}{(t.labels ?? []).slice(0, 1).map(label => <span key={label} data-type="caption" className="rounded-md bg-surface-high px-2 py-0.5 text-on-surface-var">{label}</span>)}</div>
        {(criteria.length > 0 || dependencies > 0 || (t.comment_count ?? 0) > 0) && <div data-type="caption" className="mt-s flex flex-wrap items-center gap-m text-on-surface-low">{criteria.length > 0 && <span>{exitDoneCount(criteria)}/{criteria.length} criteria</span>}{dependencies > 0 && <span>{dependencies} deps</span>}{(t.comment_count ?? 0) > 0 && <span className="inline-flex items-center gap-1"><MessageSquare size={10} />{t.comment_count}</span>}</div>}
      </div>
    </motion.div>
  </div>
}
