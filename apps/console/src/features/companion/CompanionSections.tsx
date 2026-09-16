import { useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  Activity, Bell, Check, CheckCheck, Inbox, ListTodo, MessageSquarePlus,
  Pause, Play, Square, X,
} from 'lucide-react'
import {
  api,
  type InboxItem, type Loop, type NotificationItem, type TaskItem,
  type TaskStatus, type UnifiedLoopStatus,
} from '../../shared/data/api'
import { invalidateKeys, useQuery } from '../../shared/data/data'
import { EmptyState, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { Button } from '../../shared/ui/Button'
import { TextArea } from '../../shared/ui/forms'
import { useCompanionAction } from './useCompanionAction'
import { signalPriority } from '../tasks/taskMeta'


const LIMIT = 6


const bustLoops = () => invalidateKeys('loops', true)
const bustTasks = () => invalidateKeys('tasks', true)
const bustInbox = () => invalidateKeys('inbox', true)
const bustFeed = () => invalidateKeys('notifications', true)

function Section<T>({ id, icon: Icon, title, what, query, empty, children }: {
  id: string
  icon: LucideIcon
  title: string
  what: string
  query: { data: T[] | undefined; loading: boolean; error?: unknown; refresh: () => void }
  empty: { title: string; hint: string }
  children: (rows: T[]) => React.ReactNode
}) {
  const { data, loading, error, refresh } = query
  const rows = data ?? []
  const shown = rows.slice(0, LIMIT)
  return (
    <section aria-labelledby={`${id}-heading`} className="flex flex-col gap-s">
      <h2 id={`${id}-heading`} data-type="title-m" className="flex items-center gap-s text-on-surface">
        <Icon size={16} aria-hidden className="text-on-surface-low" />
        {title}{rows.length > 0 ? ` (${rows.length})` : ''}
      </h2>
      {data === undefined && error ? (
        <LoadError what={what} error={error} onRetry={refresh} />
      ) : data === undefined && loading ? (
        <ListSkeleton rows={2} what={what} />
      ) : rows.length === 0 ? (
        <EmptyState icon={CheckCheck} title={empty.title} hint={empty.hint} />
      ) : (
        <>
          {children(shown)}
          {rows.length > shown.length && (
            <p data-type="body-m" className="text-on-surface-low">
              Showing {shown.length} of {rows.length}. Open the full dashboard for the rest.
            </p>
          )}
        </>
      )}
    </section>
  )
}

function Row({ title, sub, meta, actions }: {
  title: string; sub?: string; meta?: string; actions: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-m rounded-lg border border-outline-variant/40 px-l py-l">
      <div className="min-w-0">
        <p data-type="title-m" className="truncate text-on-surface">{title}</p>
        {sub && <p data-type="body-m" className="line-clamp-2 text-on-surface-var">{sub}</p>}
        {meta && <p data-type="body-m" className="text-on-surface-low">{meta}</p>}
      </div>
      <div className="flex flex-wrap gap-s">{actions}</div>
    </div>
  )
}

const STEERABLE: readonly UnifiedLoopStatus[] = ['running', 'paused', 'needs_input', 'stagnant', 'blocked']

export function RunningLoopsSection() {
  const query = useQuery<Loop[]>('loops-companion', () =>
    api.uLoops().then((ls) => ls.filter((l) => STEERABLE.includes(l.status))))
  const { act, view, busy } = useCompanionAction<{ status: UnifiedLoopStatus }>(query.data)
  const [nudging, setNudging] = useState<string | null>(null)
  const [text, setText] = useState('')

  const send = async (loop: Loop) => {
    const body = text.trim()
    if (!body) return
    setNudging(null)
    const ok = await act(loop.id, { status: view(loop.id, loop).status }, () => api.uLoopNudge(loop.id, body),
      `nudge ${loop.name}`, bustLoops)
    if (ok) setText('')
    else setNudging(loop.id)
  }

  return (
    <Section id="companion-loops" icon={Activity} title="Running" what="running loops" query={query}
      empty={{ title: 'Nothing running', hint: 'Loops you start appear here while they run.' }}>
      {(loops) => loops.map((raw) => {
        const l = view(raw.id, raw)
        const working = busy.has(l.id)
        return (
          <div key={l.id} className="flex flex-col gap-m">
            <Row title={l.name || l.task} sub={l.name ? l.task : undefined}
              meta={`${l.status.replace(/_/g, ' ')} · cycle ${l.total_cycles}`}
              actions={<>
                {l.status === 'running' ? (
                  <Button size="sm" variant="secondary" loading={working}
                    ariaLabel={`Pause ${l.name || l.task}`}
                    onClick={() => act(l.id, { status: 'paused' }, () => api.uLoopAction(l.id, 'pause'), `pause ${l.name}`, bustLoops)}>
                    <Pause size={15} /> Pause
                  </Button>
                ) : (
                  <Button size="sm" variant="secondary" loading={working}
                    ariaLabel={`Resume ${l.name || l.task}`}
                    onClick={() => act(l.id, { status: 'running' }, () => api.uLoopAction(l.id, 'resume'), `resume ${l.name}`, bustLoops)}>
                    <Play size={15} /> Resume
                  </Button>
                )}
                <Button size="sm" variant="secondary" ariaExpanded={nudging === l.id}
                  ariaLabel={`Nudge ${l.name || l.task}`}
                  onClick={() => { setNudging(nudging === l.id ? null : l.id); setText('') }}>
                  <MessageSquarePlus size={15} /> Nudge
                </Button>
                <Button size="sm" variant="danger" loading={working}
                  ariaLabel={`Stop ${l.name || l.task}`}
                  onClick={() => act(l.id, { status: 'stopped' }, () => api.uLoopAction(l.id, 'stop'), `stop ${l.name}`, bustLoops)}>
                  <Square size={15} /> Stop
                </Button>
              </>} />
            {nudging === l.id && (
              <div className="flex flex-col gap-s rounded-lg border border-outline-variant/40 px-l py-l">
                <TextArea value={text} onChange={setText} rows={3} autoFocus
                  ariaLabel={`What should ${l.name || l.task} do next?`}
                  placeholder="Steer it — what should it do next?" />
                {
}
                <Button size="sm" onClick={() => send(l)} disabled={!text.trim()}
                  disabledReason="Type what it should do next.">
                  Send nudge
                </Button>
              </div>
            )}
          </div>
        )
      })}
    </Section>
  )
}

const OPEN_STATUSES: readonly TaskStatus[] = ['in_progress', 'open']
const IS_OPEN: ReadonlySet<string> = new Set(OPEN_STATUSES)

export function TasksSection() {
  const query = useQuery<TaskItem[]>('tasks-companion', () =>
    Promise.all(OPEN_STATUSES.map((s) => api.tasks({ status: s, limit: 20 })))
      .then((pages) => pages.flatMap((p) => p.tasks)))
  const { act, view, busy } = useCompanionAction<{ status: TaskStatus }>(query.data)

  const move = (t: TaskItem, status: TaskStatus, verb: string) =>
    act(t.id, { status }, () => api.updateTask(t.id, { status }), `${verb} "${t.title}"`, bustTasks)

  return (
    <Section id="companion-tasks" icon={ListTodo} title="Tasks" what="tasks" query={query}
      empty={{ title: 'No open tasks', hint: 'Tasks assigned in a project appear here while they are open.' }}>
      {(tasks) => tasks
        .filter((raw) => IS_OPEN.has(view(raw.id, raw).status))
        .map((raw) => {
          const t = view(raw.id, raw)
          const working = busy.has(t.id)
          return (
            <Row key={t.id} title={t.title} sub={t.description}
              meta={[t.status.replace(/_/g, ' '), signalPriority(t.priority)?.label, t.project]
                .filter(Boolean)
                .join(' · ')}
              actions={<>
                {t.status === 'open' && (
                  <Button size="sm" variant="secondary" loading={working} ariaLabel={`Start ${t.title}`}
                    onClick={() => move(t, 'in_progress', 'start')}>
                    <Play size={15} /> Start
                  </Button>
                )}
                <Button size="sm" loading={working} ariaLabel={`Mark ${t.title} done`}
                  onClick={() => move(t, 'done', 'finish')}>
                  <Check size={15} /> Done
                </Button>
              </>} />
          )
        })}
    </Section>
  )
}

export function InboxSection() {
  const query = useQuery<InboxItem[]>('inbox-companion', () => api.inboxPending())
  const { act, view, busy } = useCompanionAction<{ status: InboxItem['status'] }>(query.data)

  const resolve = (i: InboxItem, status: 'handled' | 'dismissed', verb: string) =>
    act(i.id, { status }, () => api.updateInboxItem(i.id, { status }), `${verb} this ${i.item_kind || 'message'}`, bustInbox)

  return (
    <Section id="companion-inbox" icon={Inbox} title="Inbox" what="inbox items" query={query}
      empty={{ title: 'Inbox clear', hint: 'Messages and requests waiting on you appear here.' }}>
      {(items) => items
        .filter((raw) => view(raw.id, raw).status === 'pending')
        .map((raw) => {
          const i = view(raw.id, raw)
          const working = busy.has(i.id)
          const who = i.sender_name || i.channel_name || i.channel || 'Unknown sender'
          return (
            <Row key={i.id} title={who} sub={i.message}
              meta={[i.item_kind?.replace(/_/g, ' '), i.classification.replace(/_/g, ' ')].filter(Boolean).join(' · ')}
              actions={<>
                <Button size="sm" loading={working} ariaLabel={`Mark the message from ${who} handled`}
                  onClick={() => resolve(i, 'handled', 'resolve')}>
                  <Check size={15} /> Handled
                </Button>
                <Button size="sm" variant="secondary" loading={working}
                  ariaLabel={`Dismiss the message from ${who}`}
                  onClick={() => resolve(i, 'dismissed', 'dismiss')}>
                  <X size={15} /> Dismiss
                </Button>
              </>} />
          )
        })}
    </Section>
  )
}

export function RecentSection() {
  const query = useQuery<NotificationItem[]>('notifications-companion', () =>
    api.notifications().then((d) => d.notifications))
  const { act, view, busy } = useCompanionAction<{ acked: boolean }>(query.data)

  return (
    <Section id="companion-recent" icon={Bell} title="Recent" what="notifications" query={query}
      empty={{ title: 'Nothing recent', hint: 'Notifications your agent raises appear here.' }}>
      {(items) => items.map((raw) => {
        const n = view(raw.ts, raw)
        return (
          <Row key={n.ts} title={n.title} sub={n.body} meta={n.kind || 'info'}
            actions={n.acked ? (
              <span data-type="body-m" className="inline-flex items-center gap-xs text-on-surface-low">
                <Check size={14} aria-hidden /> read
              </span>
            ) : (
              <Button size="sm" variant="secondary" loading={busy.has(n.ts)}
                ariaLabel={`Mark "${n.title}" read`}
                onClick={() => act(n.ts, { acked: true }, () => api.ackNotification(n.ts), `mark "${n.title}" read`, bustFeed)}>
                <Check size={15} /> Mark read
              </Button>
            )} />
        )
      })}
    </Section>
  )
}
