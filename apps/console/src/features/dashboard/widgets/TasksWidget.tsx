import { useState } from 'react'
import { MoreRow } from '../../../shared/ui/MoreRow'
import { AnimatePresence } from 'framer-motion'
import { CheckCircle2, Circle, ListTodo, Plus } from 'lucide-react'
import { api } from '../../../shared/data/api'
import { useDashboardLive } from '../DashboardLive'
import { SlotEmptyState, SlotAction, WidgetRow, RowAction } from './kit'
import { ListSkeleton } from '../../../shared/ui/ListScaffold'
import { signalPriority } from '../../tasks/taskMeta'
import type { RouteProps } from '../../../app/shell/useQueryState'
import { reportingWrite } from '../../../app/shell/reportingWrite'

export function TasksWidget({ navigate }: RouteProps) {
  const { tasks, refreshAll, read } = useDashboardLive()
  const [done, setDone] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<Set<string>>(new Set())

  const complete = async (id: string, title: string) => {
    setBusy((s) => new Set(s).add(id))
    let ok = false
    try { ok = await reportingWrite(`complete “${title}”`, () => api.updateTask(id, { status: 'done' })) }
    finally { setBusy((s) => { const n = new Set(s); n.delete(id); return n }) }
    if (!ok) return
    setDone((s) => new Set(s).add(id))
    refreshAll()
  }

  const visible = tasks.filter((t) => !done.has(t.id))

  if (visible.length === 0) {
    if (!read.tasks) return <ListSkeleton rows={3} what="tasks" />
    return (
      <SlotEmptyState
        icon={ListTodo}
        action={<SlotAction icon={Plus} onClick={() => navigate('tasks/new')}>New task</SlotAction>}
      >No tasks ready to work.</SlotEmptyState>
    )
  }

  return (
    <div className="flex flex-col gap-xs pt-xs">
      <AnimatePresence initial={false}>
        {visible.slice(0, 6).map((t) => {
          const pm = signalPriority(t.priority)
          return (
          <WidgetRow
            key={t.id}
            onClick={() => navigate('tasks')}
            label={t.title}
            actions={
              busy.has(t.id)
                ? <span data-type="label-m" className="px-s text-on-surface-low">…</span>
                : (
                  <RowAction tone="ok" onClick={() => complete(t.id, t.title)} title="Mark complete"
                    ariaLabel={`Mark complete: ${t.title}`}><CheckCircle2 size={15} /></RowAction>
                )
            }
          >
            <div className="flex items-center gap-s">
              {
}
              <span className="inline-flex size-[13px] shrink-0 items-center justify-center">
                {pm && <Circle size={13} aria-label={`${pm.label} priority`} style={{ color: pm.tone }} />}
              </span>
              { }
              <span data-type="body-m" className="truncate text-on-surface" title={t.title}>{t.title}</span>
            </div>
          </WidgetRow>
          )
        })}
      {
}
      <MoreRow total={visible.length} shown={6} />
      </AnimatePresence>
      <button type="button" onClick={() => navigate('tasks/new')} className="mt-xs inline-flex items-center gap-xs self-start rounded-pill px-m py-xs text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface" data-type="label-m">
        <Plus size={13} /> New task
      </button>
    </div>
  )
}
