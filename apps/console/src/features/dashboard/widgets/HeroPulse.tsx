import { useMemo } from 'react'
import { motion } from 'framer-motion'
import {
  Activity, Inbox, ListTodo, Bell, ShieldCheck,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useDashboardLive } from '../DashboardLive'
import { physics } from '../../../shared/theme/motion'
import type { RouteProps } from '../../../app/shell/useQueryState'
import { ACTIVE_LOOP_STATUSES } from '../../../shared/data/loopStatus'


export function HeroPulse({ navigate, variant = 'strip' }: RouteProps & { variant?: 'header' | 'strip' }) {
  const header = variant === 'header'
  const {
    approvals, inbox, tasks, loops, notifications,
    approvalsErr, inboxErr, tasksErr, loopsErr, notificationsErr, read,
  } = useDashboardLive()

  const runningLoops = useMemo(
    () => loops.filter((l) => ACTIVE_LOOP_STATUSES.has(l.status)).length,
    [loops],
  )
  const unread = useMemo(() => notifications.filter((n) => !n.acked).length, [notifications])
  const inboxMsgs = useMemo(() => inbox.filter((i) => i.item_kind !== 'proposal').length, [inbox])

  type Why = 'ok' | 'pending' | 'failed'
  const pill = (n: number, err: unknown, wasRead: boolean): { n: number | null; why: Why } => {
    if (err) return { n: null, why: 'failed' }
    if (!wasRead) return { n: null, why: 'pending' }
    return { n, why: 'ok' }
  }
  const pills: { key: string; icon: LucideIcon; n: number | null; why: Why; label: string; go: string; tone: string }[] = [
    { key: 'loops', icon: Activity, ...pill(runningLoops, loopsErr, read.loops), label: runningLoops === 1 ? 'loop running' : 'loops running', go: 'projects', tone: 'var(--color-primary)' },
    { key: 'appr', icon: ShieldCheck, ...pill(approvals.length, approvalsErr, read.approvals), label: approvals.length === 1 ? 'approval waiting' : 'approvals waiting', go: 'chat', tone: 'var(--color-warn)' },
    { key: 'tasks', icon: ListTodo, ...pill(tasks.length, tasksErr, read.tasks), label: tasks.length === 1 ? 'task ready' : 'tasks ready', go: 'tasks', tone: 'var(--color-info)' },
    { key: 'inbox', icon: Inbox, ...pill(inboxMsgs, inboxErr, read.inbox), label: 'inbox', go: 'inbox', tone: 'var(--color-secondary)' },
    { key: 'notif', icon: Bell, ...pill(unread, notificationsErr, read.notifications), label: 'unread', go: 'notifications', tone: 'var(--color-on-surface-low)' },
  ]
  const reading = (p: { n: number | null; why: Why; label: string }) =>
    p.why === 'failed' ? `${p.label} — couldn’t be read`
      : p.why === 'pending' ? `Loading ${p.label}…`
        : `${p.n} ${p.label}`

  return (
    <div className={header ? 'flex items-center gap-xs' : 'flex h-full flex-wrap items-center gap-s'}>
      {pills.map((p) => (
        <button
          key={p.key}
          type="button"
          onClick={() => navigate(p.go)}
          title={header ? reading(p) : undefined}
          aria-label={reading(p)}
          className={`group flex items-center rounded-pill bg-surface-low transition-colors hover:bg-surface-high ${header ? 'gap-xs px-m py-xs' : 'gap-s px-l py-s'}`}
        >
          <p.icon size={header ? 14 : 16} style={{ color: p.tone }} className="shrink-0" />
          <motion.span
            key={`${p.key}-${p.n}`}
            initial={{ scale: 0.6, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={physics.playful}
            data-type="title-m"
            className={`tabular-nums ${p.n === null ? 'text-on-surface-low' : 'text-on-surface'}`}
          >
            {p.n === null ? '—' : p.n}
          </motion.span>
          {
}
          <span data-type="body-m" className={`text-on-surface-low group-hover:text-on-surface-var ${header ? 'hidden 2xl:inline' : ''}`}>{p.label}</span>
        </button>
      ))}
      {
}
    </div>
  )
}
