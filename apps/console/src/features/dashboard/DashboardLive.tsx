import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { useChatSocket, type WsMessage } from '../../shared/data/useChatSocket'
import { useVisiblePoll } from '../../shared/data/useVisiblePoll'
import { api } from '../../shared/data/api'
import type {
  PendingApproval, DashboardStatus, InboxItem, SkillProposal,
  Loop, TaskItem, ScheduleRun, NotificationItem, SystemInfo, DiscoverResponse, DoctorReport,
} from '../../shared/data/api'


export type ReadSlice =
  | 'approvals' | 'inbox' | 'proposals' | 'loops' | 'tasks' | 'notifications' | 'schedule'

const NOTHING_READ: Record<ReadSlice, boolean> = {
  approvals: false, inbox: false, proposals: false, loops: false, tasks: false,
  notifications: false, schedule: false,
}

export interface DashboardLiveData {
  approvals: PendingApproval[]
  inbox: InboxItem[]
  proposals: SkillProposal[]
  approvalsErr: unknown
  inboxErr: unknown
  proposalsErr: unknown
  loops: Loop[]
  tasks: TaskItem[]
  loopsErr: unknown
  tasksErr: unknown
  notificationsErr: unknown
  schedule: ScheduleRun[]
  scheduleDidIds: string[]
  scheduleSuppressed: number
  status: DashboardStatus | null
  notifications: NotificationItem[]
  system: SystemInfo | null
  discover: DiscoverResponse | null
  discoverErr: unknown
  doctor: DoctorReport | null
  doctorErr: unknown
  read: Readonly<Record<ReadSlice, boolean>>
  retryApprovals: () => void
  retryInbox: () => void
  retryProposals: () => void
  dismissDiscoverTip: (id: string) => void
  refreshAll: () => void
}

const DashboardLiveContext = createContext<DashboardLiveData | null>(null)

export function useDashboardLive(): DashboardLiveData {
  const ctx = useContext(DashboardLiveContext)
  if (!ctx) throw new Error('useDashboardLive must be used within <DashboardLiveProvider>')
  return ctx
}

const FAST_POLL = 8000
const SLOW_POLL = 20000

export function DashboardLiveProvider({ children }: { children: ReactNode }) {
  const [approvals, setApprovals] = useState<PendingApproval[]>([])
  const [inbox, setInbox] = useState<InboxItem[]>([])
  const [proposals, setProposals] = useState<SkillProposal[]>([])
  const [approvalsErr, setApprovalsErr] = useState<unknown>(null)
  const [inboxErr, setInboxErr] = useState<unknown>(null)
  const [proposalsErr, setProposalsErr] = useState<unknown>(null)
  const [loops, setLoops] = useState<Loop[]>([])
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [schedule, setSchedule] = useState<ScheduleRun[]>([])
  const [scheduleDidIds, setScheduleDidIds] = useState<string[]>([])
  const [scheduleSuppressed, setScheduleSuppressed] = useState(0)
  const [status, setStatus] = useState<DashboardStatus | null>(null)
  const [notifications, setNotifications] = useState<NotificationItem[]>([])
  const [loopsErr, setLoopsErr] = useState<unknown>(null)
  const [tasksErr, setTasksErr] = useState<unknown>(null)
  const [notificationsErr, setNotificationsErr] = useState<unknown>(null)
  const [system, setSystem] = useState<SystemInfo | null>(null)
  const [discover, setDiscover] = useState<DiscoverResponse | null>(null)
  const [discoverErr, setDiscoverErr] = useState<unknown>(null)
  const [doctor, setDoctor] = useState<DoctorReport | null>(null)
  const [doctorErr, setDoctorErr] = useState<unknown>(null)

  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const guard = <T,>(set: (v: T) => void) => (v: T) => { if (alive.current) set(v) }

  const [read, setRead] = useState<Record<ReadSlice, boolean>>(NOTHING_READ)
  const markRead = useCallback((s: ReadSlice) => {
    if (!alive.current) return
    setRead((r) => (r[s] ? r : { ...r, [s]: true }))
  }, [])

  const loadApprovals = useCallback(() => {
    api.approvals().then((d) => { guard(setApprovals)(d); guard(setApprovalsErr)(null) }).catch((e) => guard(setApprovalsErr)(e))
      .finally(() => markRead('approvals'))
  }, [markRead])
  const loadInbox = useCallback(() => {
    api.inboxPending().then((d) => { guard(setInbox)(d); guard(setInboxErr)(null) }).catch((e) => guard(setInboxErr)(e))
      .finally(() => markRead('inbox'))
  }, [markRead])
  const loadProposals = useCallback(() => {
    api.skillProposals().then((d) => { guard(setProposals)(d.proposals); guard(setProposalsErr)(null) }).catch((e) => guard(setProposalsErr)(e))
      .finally(() => markRead('proposals'))
  }, [markRead])
  const loadLoops = useCallback(() => { api.uLoops().then((d) => { guard(setLoops)(d); guard(setLoopsErr)(null) }).catch((e) => guard(setLoopsErr)(e)).finally(() => markRead('loops')) }, [markRead])
  const loadTasks = useCallback(() => { api.readyTasks().then((d) => { guard(setTasks)(d); guard(setTasksErr)(null) }).catch((e) => guard(setTasksErr)(e)).finally(() => markRead('tasks')) }, [markRead])
  const loadSchedule = useCallback(() => {
    api.triggersHistory(12).then((d) => {
      guard(setSchedule)(d.runs ?? [])
      guard(setScheduleDidIds)(d.did_ids ?? [])
      guard(setScheduleSuppressed)(d.suppressed ?? 0)
    }).catch(() => {}).finally(() => markRead('schedule'))
  }, [markRead])
  const loadStatus = useCallback(() => { api.status().then(guard(setStatus)).catch(() => {}) }, [])
  const loadNotifications = useCallback(() => { api.notifications().then((d) => { guard(setNotifications)(d.notifications ?? []); guard(setNotificationsErr)(null) }).catch((e) => guard(setNotificationsErr)(e)).finally(() => markRead('notifications')) }, [markRead])
  const loadSystem = useCallback(() => { api.system().then(guard(setSystem)).catch(() => {}) }, [])
  const loadDiscover = useCallback(() => {
    api.discover().then((d) => { guard(setDiscover)(d); guard(setDiscoverErr)(null) })
      .catch((e) => guard(setDiscoverErr)(e))
  }, [])
  const loadDoctor = useCallback(() => {
    api.doctor().then((d) => { guard(setDoctor)(d); guard(setDoctorErr)(null) })
      .catch((e) => guard(setDoctorErr)(e))
  }, [])

  const dismissDiscoverTip = useCallback(async (id: string) => {
    if (!(await reportingWrite('dismiss that tip', () => api.dismissDiscoverTip(id)))) return
    loadDiscover()
  }, [loadDiscover])

  const refreshAll = useCallback(() => {
    loadApprovals(); loadInbox(); loadProposals(); loadLoops()
    loadTasks(); loadSchedule(); loadStatus(); loadNotifications(); loadSystem(); loadDiscover(); loadDoctor()
  }, [loadApprovals, loadInbox, loadProposals, loadLoops, loadTasks, loadSchedule, loadStatus, loadNotifications, loadSystem, loadDiscover, loadDoctor])

  const workDebounce = useRef<number | undefined>(undefined)
  const refreshWork = useCallback(() => {
    if (workDebounce.current) clearTimeout(workDebounce.current)
    workDebounce.current = window.setTimeout(() => { loadLoops(); loadStatus() }, 600)
  }, [loadLoops, loadStatus])
  useEffect(() => () => { if (workDebounce.current) clearTimeout(workDebounce.current) }, [])

  const onMessage = useCallback((m: WsMessage) => {
    const t = m.type
    if (t === 'approval') loadApprovals()
    else if (t.startsWith('inbox')) loadInbox()
    else if (t.startsWith('notification')) loadNotifications()
    else if (t === 'update_progress' || t === 'chat_status' || t === 'sessions' || t.startsWith('subagent')) {
      refreshWork()
    }
  }, [loadApprovals, loadInbox, loadNotifications, refreshWork])

  useChatSocket(
    onMessage,
    refreshAll,

  )

  useEffect(() => { refreshAll() }, [refreshAll])
  useVisiblePoll(() => { loadApprovals(); loadInbox(); loadProposals(); loadLoops(); loadTasks(); loadSystem() }, FAST_POLL)
  useVisiblePoll(() => { loadSchedule(); loadStatus(); loadNotifications(); loadDiscover(); loadDoctor() }, SLOW_POLL)

  const value: DashboardLiveData = {
    approvals, inbox, proposals, approvalsErr, inboxErr, proposalsErr,
    loopsErr, tasksErr, notificationsErr, read,
    loops, tasks, schedule, scheduleDidIds, scheduleSuppressed,
    status, notifications, system,
    discover, discoverErr, doctor, doctorErr,
    retryApprovals: loadApprovals, retryInbox: loadInbox, retryProposals: loadProposals,
    dismissDiscoverTip, refreshAll,
  }
  return <DashboardLiveContext.Provider value={value}>{children}</DashboardLiveContext.Provider>
}
