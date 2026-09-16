import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fvs } from '../../shared/theme/fontWeight'
import { AnimatePresence, motion } from 'framer-motion'
import {
  ArrowLeft, Pause, Play, Square, X, Check, MessageSquarePlus,
  Play as Start, Trash2, HelpCircle, Search, ChevronRight, CornerDownRight,
  Maximize2, PanelRight, ScrollText, Download, FileText, Bot, Cpu, BarChart3, ExternalLink, ListChecks, Link2, AlertTriangle, Copy,
  FolderKanban, FolderOpen, Clock, ShieldCheck, DollarSign, Sparkles,
} from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { IconButton } from '../../shared/ui/IconButton'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { TextLink } from '../../shared/ui/TextLink'
import { Eyebrow } from '../../shared/ui/Eyebrow'
import { Button } from '../../shared/ui/Button'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { QuietButton } from '../../shared/ui/QuietButton'
import { InvestigateButton } from '../../shared/ui/InvestigateButton'
import { Spark } from '../../shared/ui/Spark'
import { Markdown } from '../../shared/ui/Markdown'
import { SidePanel } from '../../shared/ui/SidePanel'
import { Modal } from '../../shared/ui/Modal'
import { thinkingGlow } from '../../shared/theme/gradients'
import { spring, physics, messageEnter } from '../../shared/theme/motion'
import { ContentSurface } from '../../shared/ui/content/ContentSurface'
import { resolveContentType } from '../../shared/ui/content/contentTypes'
import { api, type GoalLoop, type LoopFinding, type LoopNudge, type LoopVerdict, type Artifact, type TaskItem, type LoopSpend } from '../../shared/data/api'
import { loopSpendPill, loopSpendTitle } from '../../shared/data/runCost'
import { peekQuery, writeQuery } from '../../shared/data/data'
import { downloadText, safeFilename } from '../../shared/data/download'
import { useRunStream } from './useRunStream'
import { loopToGoalLoop } from './goalAdapter'
import { RunPhaseTrail } from './RunPhaseTrail'
import { foldReducer, emptyRunFlags, type RunFlags } from './runFold'
import { activePhaseIndex, phaseMinCycles, phaseForCycle } from './loopPhases'
import { useChatSocket, type WsMessage } from '../../shared/data/useChatSocket'
import { belongsToLoop } from '../workflows/containerKey'
import { type SkillUsed, skillsUsedLabel, skillsUsedTitle } from '../chat/chatTypes'
import { useQueryFlag, type RouteProps } from '../../app/shell/useQueryState'
import { accentChip } from '../../shared/theme/accent'
import { tabListKeys } from '../../shared/data/tabListKeys'
import { loopStatusLabel, effectiveLoopStatus, ACTIVE_LOOP_STATUSES, LOOP_ACTION_SOURCE_STATUSES } from '../../shared/data/loopStatus'
import { notify } from '../../app/shell/appSdk'
import { copyText } from '../../app/shell/clipboard'

function parseSel(raw?: string): { kind: 'log' } | { kind: 'roi' } | { kind: 'cycle'; cycle: number } | null {
  if (!raw) return null
  if (raw === 'log') return { kind: 'log' }
  if (raw === 'roi') return { kind: 'roi' }
  if (raw.startsWith('cycle-')) { const n = Number(raw.slice(6)); if (!Number.isNaN(n)) return { kind: 'cycle', cycle: n } }
  return null
}


const GOAL_TYPE_LABEL: Record<string, string> = { verifiable: 'Verifiable', open_ended: 'Open-ended', monitor: 'Monitor' }
const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s)

const DIAL_THRESHOLD: Record<string, number | null> = {
  quick: 3.0, balanced: 2.0, exhaustive: 1.0, forever: null,
}


interface RoiPoint { cycle: number; score: number }

function RoiRail({ points, granularity }: { points: RoiPoint[]; granularity: string }) {
  if (!points.length) return null
  const threshold = DIAL_THRESHOLD[granularity] ?? null
  const max = 5
  const H = 96
  const recent = points.slice(-40)
  return (
    <div className="flex gap-s">
      { }
      <div data-type="caption" className="relative w-5 shrink-0 text-on-surface-low tabular-nums" style={{ height: H }}>
        <span className="absolute right-0 -translate-y-1/2" style={{ top: 0 }}>5</span>
        <span className="absolute right-0 -translate-y-1/2" style={{ top: H / 2 }}>2.5</span>
        <span className="absolute right-0 -translate-y-1/2" style={{ top: H }}>0</span>
      </div>
      { }
      <div className="relative flex-1 min-w-0">
        <div className="relative" style={{ height: H }}>
          { }
          {threshold != null && (
            <div className="absolute left-0 right-0 border-t border-dashed" title={`Stop threshold (${granularity})`}
              style={{ top: H * (1 - threshold / max), borderColor: 'color-mix(in srgb, var(--color-primary) 55%, transparent)' }}>
              <span data-type="caption" className="absolute right-0 -top-3.5 tabular-nums text-primary">thr {threshold.toFixed(0)}</span>
            </div>
          )}
          {
}
          <div className="absolute inset-0 flex items-end gap-[3px]">
            {recent.map((p) => {
              const below = threshold != null && p.score < threshold
              return (
                <motion.div key={p.cycle} className="flex-1 min-w-[5px] rounded-t-[2px]" title={`cycle ${p.cycle}: ${p.score.toFixed(1)}`}
                  initial={false}
                  animate={{ height: `${Math.max(3, (p.score / max) * 100)}%`, opacity: below ? 0.45 : 1 }}
                  transition={physics.fluid}
                  style={{ background: below ? 'var(--color-on-surface-low)' : 'var(--color-primary)' }} />
              )
            })}
          </div>
        </div>
        { }
        <div className="flex gap-[3px] mt-1">
          {recent.map((p, i) => {
            const show = recent.length <= 16 || i === 0 || i === recent.length - 1 || p.cycle % 5 === 0
            return <span key={p.cycle} data-type="caption" className="flex-1 min-w-[5px] text-center text-on-surface-low tabular-nums">{show ? p.cycle : ''}</span>
          })}
        </div>
      </div>
    </div>
  )
}

function shortModel(m: string): string {
  const afterColon = m.includes(':') ? m.split(':').pop()! : m
  const parts = afterColon.split('.')
  return (parts.pop() || afterColon).replace(/\[.*\]$/, '')
}

function MetaPill({ icon, text, tone, title }: { icon?: React.ReactNode; text: string; tone?: 'primary'; title?: string }) {
  return (
    <span title={title}
      data-type="caption" className="inline-flex items-center gap-1 rounded-pill px-2 h-5 max-w-[14rem] truncate"
      style={tone === 'primary'
        ? accentChip
        : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }}>
      {icon}<span className="truncate">{text}</span>
    </span>
  )
}

function fmt(sec: number): string {
  if (!sec || sec < 1) return '0s'
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60)
  if (h) return `${h}h ${m}m`
  return m ? `${m}m ${s}s` : `${s}s`
}

function asText(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (Array.isArray(v)) return v.map(asText).filter(Boolean).join(', ')
  if (typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => `${k}: ${asText(val)}`).join(' · ')
  }
  return String(v)
}

export function LoopCockpitPage({ id, onBack, onDeleted, onOpenArtifact, onOpenTask, onOpenProject, query, setQuery }: { id: string; onBack: () => void; onDeleted?: () => void; onOpenArtifact?: (slug: string) => void; onOpenTask?: (taskId: string) => void; onOpenProject?: (projectId: string) => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const q = query
  const sq = setQuery
  const [c, setC] = useState<GoalLoop | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [spend, setSpend] = useState<LoopSpend | null>(null)
  const [report, setReport] = useState('')
  const [log, setLog] = useState('')
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const railOpen = q.details === '1'
  const setRailOpen = (v: boolean) => sq(v ? { details: '1' } : { details: null, sel: null })
  const selected = parseSel(q.sel)
  const setSelected = (s: { kind: 'log' } | { kind: 'roi' } | { kind: 'cycle'; cycle: number } | null) =>
    sq({ sel: !s ? null : s.kind === 'cycle' ? `cycle-${s.cycle}` : s.kind })
  const [nudgeOpen, setNudgeOpen] = useQueryFlag(q, sq, 'nudge')
  const [reportOpen, setReportOpen] = useQueryFlag(q, sq, 'report')
  const [promptOpen, setPromptOpen] = useQueryFlag(q, sq, 'prompt')
  const [nudgeText, setNudgeText] = useState('')
  const [nudgeSending, setNudgeSending] = useState(false)
  const [nudgeError, setNudgeError] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [confirmStop, setConfirmStop] = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const cancelRename = useRef(false)
  const renameInFlight = useRef(false)
  const [runFlags, setRunFlags] = useState<RunFlags>(emptyRunFlags)
  const judgeDegraded = runFlags.judgeDegraded
  const [linkCopied, setLinkCopied] = useState(false)
  const [projName, setProjName] = useState('')
  const projId = c?.project_id || c?.tasks_project_id || ''
  useEffect(() => {
    if (!projId) { setProjName(''); return }
    let alive = true
    api.project(projId).then((pr) => { if (alive) setProjName(pr.name) }).catch(() => {})
    return () => { alive = false }
  }, [projId])
  const [statusText, setStatusText] = useState('')
  const [activity, setActivity] = useState<{ kind: string; label: string; detail?: string }[]>([])
  const [now, setNow] = useState(() => 0)


  const loadReport = useRef<() => void>(() => {})
  const everLoaded = useRef(false)
  useEffect(() => {
    let alive = true
    everLoaded.current = false
    const loadOutputs = () => {
      api.uLoopReport(id).then((rep) => { if (alive && rep) { setReport(rep.report || ''); setLog(rep.log || '') } }).catch(() => {})
      api.artifacts({ tag: `loop:${id}` }).then((a) => { if (alive) setArtifacts(a) }).catch(() => {})
    }
    const loadTasks = (ids: string[]) => {
      if (!ids.length) { setTasks([]); return }
      Promise.all(ids.map((tid) => api.task(tid).catch(() => null)))
        .then((ts) => { if (alive) setTasks(ts.filter(Boolean) as TaskItem[]) }).catch(() => {})
    }
    let t = 0
    const TERMINAL = ['complete', 'stopped', 'failed']
    const load = async () => {
      const raw = await api.uLoop(id).catch(() => null)
      const gl = raw ? loopToGoalLoop(raw) : null
      if (!alive) return
      if (gl) {
        everLoaded.current = true; setC(gl); setNotFound(false); writeQuery(`loop:${id}`, gl); loadOutputs(); loadTasks(gl.linked_task_ids ?? [])
        setSpend(raw?.spend ?? null)
        if (t && TERMINAL.includes(gl.status)) { clearInterval(t); t = 0 }
      }
      else if (!everLoaded.current) {
        setNotFound(true)
        if (t) { clearInterval(t); t = 0 }
      }
    }
    loadReport.current = loadOutputs
    load()
    t = window.setInterval(load, 30_000)
    return () => { alive = false; if (t) clearInterval(t) }
  }, [id])

  useEffect(() => {
    if (c === null) {
      const seed = peekQuery<GoalLoop>(`loop:${id}`)
      if (seed) setC(seed)
    }
  }, [id, c])

  const { connected } = useRunStream(id, !notFound, {
    onSnapshot: (l) => setC(loopToGoalLoop(l)),
    onLifecycle: (event, data) => {
      setRunFlags((f) => foldReducer(f, event, data))
      loadReport.current()
    },
  })

  useEffect(() => {
    setNow(Date.now() / 1000)
    if (!c || !ACTIVE_LOOP_STATUSES.has(c.status)) return
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(t)
  }, [c?.status])

  useEffect(() => {
    if (!confirmDelete) return
    const t = window.setTimeout(() => setConfirmDelete(false), 4000)
    return () => clearTimeout(t)
  }, [confirmDelete])
  useEffect(() => {
    if (!confirmStop) return
    const t = window.setTimeout(() => setConfirmStop(false), 4000)
    return () => clearTimeout(t)
  }, [confirmStop])

  const [skillsUsed, setSkillsUsed] = useState<SkillUsed[]>([])
  const workerKey = c?.session_key ?? ''
  const cyclesSeen = c?.total_cycles ?? 0
  useEffect(() => {
    if (!workerKey) { setSkillsUsed([]); return }
    let alive = true
    api.chatSessionDetail(workerKey).then((d) => {
      if (!alive) return
      let last: SkillUsed[] = []
      for (const m of d.messages || []) {
        if (m.role !== 'assistant') continue
        const s = m.meta?.skills_used
        if (Array.isArray(s) && s.length) last = s
      }
      setSkillsUsed(last)
    }).catch(() => { if (alive) setSkillsUsed([]) })
    return () => { alive = false }
  }, [workerKey, cyclesSeen])

  const onWs = useCallback((m: WsMessage) => {
    if (!belongsToLoop(m.data?.session as string | undefined, id)) return
    if (m.type === 'chat_status') { const s = String(m.data.status ?? ''); setStatusText(s); setActivity((a) => [...a, { kind: 'status', label: s }].slice(-40)) }
    else if (m.type === 'tool_call') setActivity((a) => [...a, { kind: 'tool', label: String(m.data.tool ?? 'tool'), detail: String(m.data.purpose ?? m.data.input_preview ?? '') }].slice(-40))
    else if (m.type === 'activity_event') {
      const text = String(m.data.text ?? '')
      if (text) {
        if (String(m.data.kind ?? '') === 'status') setStatusText(text)
        setActivity((a) => [...a, { kind: 'status', label: text }].slice(-40))
      }
    }
  }, [id])
  useChatSocket(onWs)

  if (!c) {
    if (notFound) return (
      <div className="flex h-full flex-col items-center justify-center gap-m px-l text-center">
        <div className="text-on-surface text-[1.0625rem]" style={fvs(500)}>Loop not found</div>
        <p data-type="body-s" className="max-w-md text-on-surface-low">This loop doesn’t exist — it may have been deleted, or the link is out of date.</p>
        <Button size="sm" onClick={onBack}><ArrowLeft size={15} /> Back to loops</Button>
      </div>
    )
    return <div className="flex h-full items-center justify-center text-on-surface-low">Loading…</div>
  }
  const active = ACTIVE_LOOP_STATUSES.has(c.status)
  const running = c.status === 'running'
  const findings = [...(c.findings ?? [])].sort((a, b) => b.cycle - a.cycle)
  const verdictByCycle = new Map<number, LoopVerdict>()
  for (const v of c.verdicts ?? []) {
    if (typeof v.cycle !== 'number') continue
    const prev = verdictByCycle.get(v.cycle)
    const scored = typeof v.marginal_value === 'number'
    const prevScored = typeof prev?.marginal_value === 'number'
    if (prev == null || (scored && !prevScored)) verdictByCycle.set(v.cycle, v)
  }
  const roiPoints: RoiPoint[] = (c.verdicts ?? []).some((v) => typeof v.marginal_value === 'number')
    ? (c.verdicts ?? [])
        .filter((v) => typeof v.cycle === 'number' && typeof v.marginal_value === 'number')
        .map((v) => ({ cycle: v.cycle as number, score: v.marginal_value as number }))
        .sort((a, b) => a.cycle - b.cycle)
    : (c.marginal_scores ?? []).map((s, i) => ({ cycle: i + 1, score: s }))
  const { byCycle, pending } = groupNudges(c.nudges ?? [])
  const W = 'calc(var(--content-width) + 340px)'

  const cycleTs = (c.findings ?? []).map((f) => f.ts).filter((t): t is number => typeof t === 'number').sort((a, b) => a - b)
  const banked = c.elapsed_seconds ?? 0
  const curStretch = running && c.started_at ? Math.max(0, now - c.started_at) : 0
  const totalElapsed = banked + curStretch
  const lastTs = cycleTs.at(-1) ?? c.started_at ?? now
  const curCycleElapsed = active ? Math.max(0, now - lastTs) : 0

  async function act(a: 'start' | 'pause' | 'resume' | 'stop') {
    const next = await api.uLoopAction(id, a).catch(() => null); if (next) setC(loopToGoalLoop(next))
  }
  async function sendNudge() {
    const t = nudgeText.trim(); if (!t || nudgeSending) return
    setNudgeSending(true); setNudgeError(false)
    try {
      await api.uLoopNudge(id, t)
      setNudgeText(''); setNudgeOpen(false)
    } catch {
      setNudgeError(true)
    } finally {
      setNudgeSending(false)
    }
  }
  async function del() {
    if (!confirmDelete) { setConfirmDelete(true); return }
    try { await api.deleteULoop(id) }
    catch (e) { notify(`Couldn't delete this loop: ${String((e as Error)?.message || e)}`, 'error'); return }
    onDeleted ? onDeleted() : onBack()
  }
  function copyLink() {
    const url = `${location.origin}/#/loops/${id}`
    void copyText(url, 'the link').then((ok) => { if (ok) { setLinkCopied(true); setTimeout(() => setLinkCopied(false), 1500) } })
  }
  function startRename() { cancelRename.current = false; setTitleDraft(c?.name || ''); setEditingTitle(true) }
  function abortRename() { cancelRename.current = true; setEditingTitle(false) }
  async function commitRename() {
    setEditingTitle(false)
    if (cancelRename.current) { cancelRename.current = false; return }
    if (renameInFlight.current) return
    const name = titleDraft.trim()
    if (!name || name === (c?.name || '')) return
    renameInFlight.current = true
    try {
      const updated = await api.updateULoop(id, { name }).catch(() => null)
      if (updated) setC(loopToGoalLoop(updated))
    } finally { renameInFlight.current = false }
  }

  const execPlan = (c.execution_plan ?? []) as Record<string, unknown>[]
  const activePhase = execPlan.length ? activePhaseIndex(c.total_cycles, execPlan) : -1

  const statusLine = (
    <span data-type="body-s" className="inline-flex items-center gap-s text-on-surface-var truncate">
      {running ? (
        <span className="relative inline-flex items-center justify-center size-4">
          <motion.span aria-hidden className="absolute inset-[-7px] rounded-pill" style={{ background: thinkingGlow() }}
            animate={{ opacity: [0.4, 0.9, 0.4] }} transition={{ duration: 3.2, ease: 'easeInOut', repeat: Infinity }} />
          <Spark size={13} />
        </span>
      ) : (
        <span className="size-1.5 rounded-pill" style={{ background: c.status === 'failed' ? 'var(--color-danger)' : c.status === 'complete' ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
      )}
      {running ? (statusText || 'Working') : loopStatusLabel(effectiveLoopStatus(c.status, c.error_message))}
      {
}
      {running && (
        <span className="inline-flex shrink-0 items-center gap-1 text-on-surface-low">
          <span className="inline-block size-1.5 rounded-pill"
            style={{ background: connected ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }} />
          {connected ? 'Streaming' : 'Connecting…'}
        </span>
      )}
    </span>
  )

  const wsDir = (c as { workspace_dir?: string }).workspace_dir || ''
  const cycleLabel = (() => {
    const shown = running ? c.total_cycles + 1 : c.total_cycles
    return c.max_cycles === 0 ? `cycle ${shown} · ongoing` : `cycle ${shown}/${c.max_cycles}`
  })()
  const statusBar = (
    <div className="shrink-0 flex flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-outline-variant/30 px-2xl py-1.5"
      style={{ background: 'var(--color-surface-container)' }}>
      { }
      {execPlan.length > 0
        ? <RunPhaseTrail plan={execPlan} activePhase={activePhase} active={active} complete={c.status === 'complete'} findings={findings} compact />
        : <span data-type="caption" className="text-on-surface-var tabular-nums">{cycleLabel}</span>}
      {c.started_at != null && <MetaPill icon={<Clock size={11} />} text={fmt(totalElapsed)} title="Elapsed (running time)" />}
      {
}
      {spend !== null && <MetaPill icon={<DollarSign size={11} />} text={loopSpendPill(spend)} title={loopSpendTitle(spend)} />}
      {
}
      {skillsUsed.length > 0 && <MetaPill icon={<Sparkles size={11} />} text={skillsUsedLabel(skillsUsed)} title={skillsUsedTitle(skillsUsed)} />}
      <span className="flex-1" />
      { }
      {projId && projName && (onOpenProject
        ? <button type="button" onClick={() => onOpenProject(projId)} title={`Project: ${projName} — open`}
            data-type="caption" className="inline-flex items-center gap-1 rounded-pill px-2 h-5 max-w-[14rem] hover:brightness-110"
            style={accentChip}>
            <FolderKanban size={11} className="shrink-0" /><span className="truncate">{projName}</span>
          </button>
        : <MetaPill icon={<FolderKanban size={11} />} text={projName} tone="primary" title="Project" />)}
      {wsDir && <MetaPill icon={<FolderOpen size={11} />} text={wsDir.split('/').pop() || wsDir} title={`Workspace: ${wsDir}`} />}
      <MetaPill icon={<Bot size={11} />} text={c.agent || 'default'} title="Worker agent" />
      {c.model && <MetaPill icon={<Cpu size={11} />} text={shortModel(c.model)} title={c.model} />}
      <MetaPill text={c.attended ? 'Attended' : 'Unattended'} title="Mode" />
      {(c as { kind?: string }).kind === 'goal' && <>
        <MetaPill text={GOAL_TYPE_LABEL[c.goal_type] ?? c.goal_type} tone="primary" title="Goal type" />
        <MetaPill text={cap(c.granularity)} title="Granularity" />
      </>}
    </div>
  )

  return (
    <div className="flex h-full flex-col">
      {
}
      <TopBar
        keepCornerPadding
        left={
          <div className="flex items-center gap-s min-w-0">
            <IconButton icon={ArrowLeft} label="Back to loops" size={40} onClick={onBack} />
            <div className="min-w-0 flex flex-col">
              {editingTitle ? (
                <input autoFocus aria-label="Rename this loop" value={titleDraft} onChange={(e) => setTitleDraft(e.target.value)}
                  onBlur={commitRename} onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); else if (e.key === 'Escape') abortRename() }}
                  data-type="body-m" className="min-w-[16rem] h-7 rounded-md bg-surface-high px-2 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
              ) : (
                <button type="button" onClick={startRename} title="Rename loop"
                  data-type="title-m" className="truncate text-on-surface leading-tight text-left hover:text-on-surface-var" style={fvs(600)}>
                  {c.name || c.goal}
                </button>
              )}
              {statusLine}
            </div>
            { }
            <IconButton icon={linkCopied ? Check : Link2} label={linkCopied ? 'Link copied' : 'Copy loop link'} size={32} onClick={copyLink} />
          </div>
        }
        right={
          <HeaderActions className="max-w-[60vw]">
            {!active && <HeaderControl icon={Trash2} label={confirmDelete ? 'Confirm delete?' : 'Delete'} danger priority="low" onClick={del} />}
            {LOOP_ACTION_SOURCE_STATUSES.start.has(c.status) && <HeaderControl icon={Start} label="Start" variant="primary" priority="primary" onClick={() => act('start')} />}
            {LOOP_ACTION_SOURCE_STATUSES.pause.has(c.status) && <HeaderControl icon={Pause} label="Pause" variant="secondary" priority="primary" onClick={() => act('pause')} />}
            {LOOP_ACTION_SOURCE_STATUSES.resume.has(c.status) && <HeaderControl icon={Play} label="Resume" variant="primary" priority="primary" onClick={() => act('resume')} />}
            {LOOP_ACTION_SOURCE_STATUSES.stop.has(c.status) && <HeaderControl icon={Square} label={confirmStop ? 'Stop for good?' : 'Stop'} variant={confirmStop ? 'danger' : 'secondary'} onClick={() => { if (!confirmStop) { setConfirmStop(true); return } setConfirmStop(false); act('stop') }} />}
            {
}
            {active && <HeaderControl icon={MessageSquarePlus} label="Nudge" variant="secondary" ariaExpanded={nudgeOpen} onClick={() => setNudgeOpen(!nudgeOpen)} />}
            <HeaderControl icon={PanelRight} label="Details" active={railOpen} onClick={() => { setRailOpen(!railOpen); setSelected(null) }} />
          </HeaderActions>
        }
      />

      { }
      {statusBar}

      <div className="flex-1 min-h-0 flex">
       { }
       <div className="flex-1 min-w-0 overflow-hidden flex flex-col">
        <div className="shrink-0 px-2xl pt-m pb-m flex flex-col gap-m" style={{ marginInline: 'auto', width: '100%', maxWidth: W }}>
          {
}
          <div className="rounded-lg bg-surface-container/60 px-l py-m">
            <button type="button" onClick={() => setPromptOpen(!promptOpen)} aria-expanded={promptOpen} className="flex items-center gap-s text-left w-full min-w-0">
              <ChevronRight size={14} className={`shrink-0 text-on-surface-low transition-transform ${promptOpen ? 'rotate-90' : ''}`} />
              <Eyebrow as="span" className="shrink-0">Prompt</Eyebrow>
              { }
              {!promptOpen && (
                <span data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface-var">
                  {(c.goal || '').split('\n').map((l) => l.trim()).find(Boolean) || '—'}
                </span>
              )}
            </button>
            {promptOpen && <div className="mt-2" />}
            {promptOpen
              ? <div data-type="body-m" className="max-h-[40vh] overflow-y-auto text-on-surface">
                  <Markdown>{c.goal}</Markdown>
                  {c.success_criteria && <p data-type="body-s" className="mt-2 text-on-surface-low"><span className="text-on-surface-var">Done when:</span> {c.success_criteria}</p>}
                  {
}
                  {c.sub_goals.length > 0 && (
                    <div className="mt-l">
                      <Eyebrow className="mb-1.5">Sub-goals · {c.sub_goals.length}</Eyebrow>
                      <ul className="flex flex-col gap-1.5">
                        {c.sub_goals.map((s, i) => (
                          <li key={i} data-type="body-s" className="flex items-start gap-s text-on-surface-var">
                            <span className="mt-1.5 size-1 shrink-0 rounded-pill bg-primary" />{asText(s)}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {
}
                  {c.execution === 'multi_agent' && (c.roster?.length ?? 0) > 0 && (
                    <div className="mt-l">
                      <Eyebrow className="mb-1.5">
                        Roster · {c.roster!.length}{c.strategy_id ? ` · ${cap(c.strategy_id.replace(/_/g, ' '))}` : ''}
                      </Eyebrow>
                      <div className="flex flex-col gap-1.5">
                        {c.roster!.map((m, i) => (
                          <div key={i} className="flex flex-col gap-0.5 rounded-lg bg-surface-container px-m py-2">
                            <span data-type="label-s" className="text-on-surface" style={fvs(550)}>{m.role}</span>
                            {m.persona && <span data-type="body-s" className="text-on-surface-var">{m.persona}</span>}
                            {m.role_hint && <span data-type="caption" className="text-on-surface-low mt-0.5">↳ {m.role_hint}</span>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              : <div data-type="body-s" className="pl-[22px] text-on-surface-var line-clamp-2 break-words"><Markdown>{c.goal}</Markdown></div>}
          </div>

          {c.error_message && (() => {
            const info = c.status === 'complete'
            const tone = info ? 'var(--color-on-surface-low)' : 'var(--color-danger)'
            const Icon = info ? FileText : AlertTriangle
            return (
            <motion.div variants={messageEnter} initial="initial" animate="animate" data-type="body-s" className="rounded-md px-m py-2.5" style={{ background: `color-mix(in srgb, ${tone} 12%, transparent)`, color: tone }}>
              <div className="flex items-center gap-1.5 mb-1" style={fvs(500)}>
                <Icon size={14} className="shrink-0" /> {c.status === 'failed' ? 'This loop stopped on an error' : info ? 'Completed on its cycle budget' : 'Last cycle hit an error'}
              </div>
              <p className="break-words opacity-90">{c.error_message}</p>
              {
}
              {LOOP_ACTION_SOURCE_STATUSES.resume.has(c.status) && <p className="mt-1.5 opacity-75">Fix the underlying cause, then <span style={fvs(500)}>Resume</span> to continue from where it left off.</p>}
            </motion.div>
          )})()}
          {judgeDegraded && running && (
            <div data-type="body-s" className="rounded-md px-m py-2 flex items-center gap-2" style={{ background: 'color-mix(in srgb, var(--color-warning) 12%, transparent)', color: 'var(--color-warning)' }}>
              <AlertTriangle size={14} className="shrink-0" /> Done-ness check was unavailable on a recent cycle — the loop keeps running on its cycle budget. It’ll resume quality assessment automatically.
            </div>
          )}
          {c.status === 'needs_input' && c.pending_question && (
            <div data-type="body-s" className="rounded-md px-m py-2.5" style={{ background: 'color-mix(in srgb, var(--color-info) 12%, transparent)' }}>
              <div className="flex items-center gap-1.5 text-info mb-1" style={fvs(500)}><HelpCircle size={14} /> The agent needs your input</div>
              <div className="text-on-surface">{c.pending_question}</div>
              {
}
              {!nudgeOpen && (
                <Button size="sm" className="mt-2" onClick={() => setNudgeOpen(true)}>
                  <MessageSquarePlus size={14} /> Answer & resume
                </Button>
              )}
            </div>
          )}

          <AnimatePresence>
            {nudgeOpen && (
              <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
                <div className="rounded-lg bg-surface-container p-m">
                  <textarea autoFocus value={nudgeText} onChange={(e) => { setNudgeText(e.target.value); if (nudgeError) setNudgeError(false) }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); sendNudge() }
                      else if (e.key === 'Escape') { e.preventDefault(); setNudgeOpen(false); setNudgeText(''); setNudgeError(false) }
                    }}
                    placeholder="Guide the next cycle — focus an angle, or answer the agent's question."
                    data-type="body-s" className="w-full bg-transparent outline-none focus:ring-2 focus:ring-inset focus:ring-primary text-on-surface placeholder:text-on-surface-low min-h-[60px] resize-y" />
                  {nudgeError && (
                    <p role="alert" data-type="body-s" className="mt-1" style={{ color: 'var(--color-error)' }}>Couldn’t send the nudge — your text is kept, try again.</p>
                  )}
                  <div className="flex items-center justify-end gap-s mt-2">
                    <span data-type="caption" className="mr-auto text-on-surface-low">⌘↵ to send · Esc to cancel</span>
                    <Button variant="ghost" size="sm" onClick={() => { setNudgeOpen(false); setNudgeText(''); setNudgeError(false) }}>Cancel</Button>
                    <Button size="sm" onClick={sendNudge} disabled={!nudgeText.trim() || nudgeSending}
                      disabledReason={!nudgeText.trim() ? 'Write a nudge first' : undefined}>{nudgeSending ? 'Sending…' : 'Send nudge'}</Button>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {
}
        <div className="flex-1 min-h-0 px-2xl pb-2xl flex flex-col gap-l" style={{ marginInline: 'auto', width: '100%', maxWidth: W }}>
          {
}
          <OutputsPanel
            loop={c} artifacts={artifacts} tasks={tasks} report={report} active={active}
            onOpenArtifact={onOpenArtifact}
            onOpenTask={onOpenTask}
            onExpandReport={() => setReportOpen(true)}
            onDownloadReport={() => downloadText(`${safeFilename(c.name || c.goal, c.id)}-deliverable.md`, report, 'text/markdown;charset=utf-8')}
          />
        </div>
       </div>

       { }
       <AnimatePresence>
         {railOpen && (
           <SidePanel
             key="details"
             storeKey="loop-rail-w"
             fillHeight
             icon={selected
               ? <SquareIconButton icon={ArrowLeft} iconSize={16} label="Back to list" onClick={() => setSelected(null)} />
               : <PanelRight size={18} />}
             title={selected == null ? 'Details' : selected.kind === 'log' ? 'Findings Log' : selected.kind === 'roi' ? 'Returns per cycle' : `Cycle ${selected.cycle}`}
             onClose={() => { setRailOpen(false); setSelected(null) }}
           >
             {selected == null ? (
               <div className="flex flex-col gap-1">
                 <RailRow
                   icon={<ScrollText size={15} />}
                   label="Findings Log"
                   hint={`${findings.length} ${findings.length === 1 ? 'cycle' : 'cycles'}`}
                   onClick={() => setSelected({ kind: 'log' })}
                 />
                 {c.goal_type === 'open_ended' && (c.marginal_scores?.length ?? 0) > 0 && (
                   <RailRow
                     icon={<BarChart3 size={15} />}
                     label="Returns per cycle"
                     hint={`▲${c.marginal_scores!.at(-1)!.toFixed(1)}`}
                     onClick={() => setSelected({ kind: 'roi' })}
                   />
                 )}
                 {pending.length > 0 && (
                   <div data-type="body-s" className="rounded-lg px-m py-2" style={{ background: 'color-mix(in srgb, var(--color-info) 8%, transparent)', border: '1px dashed color-mix(in srgb, var(--color-info) 30%, transparent)' }}>
                     <Eyebrow tone="info" className="flex items-center gap-1.5 mb-1"><MessageSquarePlus size={12} /> nudge queued — applies next cycle</Eyebrow>
                     {pending.map((n, i) => <p key={i} className="text-on-surface-var">{n.text}</p>)}
                   </div>
                 )}
                 {(() => {
                   const cycleNode = (f: LoopFinding, idx: number) => {
                     const sorted = cycleTs
                     const ti = sorted.indexOf(f.ts ?? -1)
                     const prev = ti > 0 ? sorted[ti - 1] : c.started_at ?? undefined
                     const dur = f.ts != null && prev != null ? f.ts - prev : undefined
                     return <CycleNode key={f.cycle} f={f} verdict={verdictByCycle.get(f.cycle)} dur={dur} hasNudge={!!byCycle.get(f.cycle)?.length} onClick={() => setSelected({ kind: 'cycle', cycle: f.cycle })} delay={idx * 0.02} />
                   }
                   const liveCycle = active ? (
                     <div className="rounded-lg bg-surface-container px-m py-2">
                       <div className="flex items-center gap-s">
                         <span className="relative inline-flex items-center justify-center size-5 shrink-0">
                           {running && <motion.span aria-hidden className="absolute inset-[-6px] rounded-pill" style={{ background: thinkingGlow() }} animate={{ opacity: [0.3, 0.7, 0.3] }} transition={{ duration: 3, repeat: Infinity }} />}
                           <span className={running ? '' : 'text-on-surface-low'}><Spark size={13} /></span>
                         </span>
                         <span data-type="label-s" className="flex-1 truncate text-on-surface" style={fvs(500)}>Cycle {c.total_cycles + 1} · {running ? (statusText || 'working') : loopStatusLabel(effectiveLoopStatus(c.status, c.error_message)).toLowerCase()}</span>
                         {running && <span data-type="caption" className="shrink-0 text-on-surface-low tabular-nums">{fmt(curCycleElapsed)}</span>}
                       </div>
                       {running && activity.length > 0 && <LiveSubsteps activity={activity} />}
                     </div>
                   ) : null
                   if (execPlan.length === 0) {
                     const emptyNote = findings.length === 0 && !active
                       ? (c.total_cycles > 0
                           ? `${c.total_cycles} ${c.total_cycles === 1 ? 'cycle' : 'cycles'} ran — no per-cycle detail recorded for this loop.`
                           : 'No cycles completed yet.')
                       : null
                     return (<>
                       {liveCycle}
                       {emptyNote && <p data-type="body-s" className="text-on-surface-low px-m py-1">{emptyNote}</p>}
                       {findings.map((f, idx) => cycleNode(f, idx))}
                     </>)
                   }
                   return execPlan.map((_ph, pi) => pi).reverse().map((pi) => (
                     <PhaseGroup key={pi} phase={execPlan[pi]} index={pi} active={pi === activePhase}
                       minCycles={phaseMinCycles(execPlan[pi])}
                       cycles={findings.filter((f) => phaseForCycle(f.cycle, execPlan) === pi)}
                       renderCycle={cycleNode} liveCycle={pi === activePhase ? liveCycle : null} />
                   ))
                 })()}
               </div>
             ) : (
               selected.kind === 'roi'
                 ? (<div className="flex flex-col gap-m">
                     <p data-type="body-s" className="text-on-surface-var">The judge's marginal value per cycle, against your <span className="text-on-surface">{c.granularity}</span> stop threshold. A run of bars below the line is what trips the auto-stop.</p>
                     <div className="rounded-lg bg-surface-container px-m py-l">
                       <RoiRail points={roiPoints} granularity={c.granularity} />
                     </div>
                   </div>)
                 : selected.kind === 'log'
                 ? (log
                   ? (<div className="flex flex-col gap-m">
                       <div className="flex justify-end">
                         <QuietButton onClick={() => downloadText(`${safeFilename(c.name || c.goal, c.id)}-findings.md`, log, 'text/markdown;charset=utf-8')}><Download size={13} /> Download</QuietButton>
                       </div>
                       <Markdown>{log}</Markdown>
                     </div>)
                   : <p data-type="body-s" className="text-on-surface-low">No findings logged yet — the cumulative trail appears here as cycles complete.</p>)
                 : (() => {
                     const f = findings.find((x) => x.cycle === selected.cycle)
                     if (!f) return <p data-type="body-s" className="text-on-surface-low">Cycle not found.</p>
                     return (<>
                       {
}
                       <div className="mb-s flex justify-end">
                         <InvestigateButton kind="loop_cycle" id={`${c.id}:${f.cycle}`}
                           backLink={`#/loops/${c.id}`} size={28} />
                       </div>
                       <CycleDetail f={f} verdict={verdictByCycle.get(f.cycle)} nudges={byCycle.get(f.cycle) ?? []} activity={running && c.total_cycles === f.cycle ? activity : []} />
                     </>)
                   })()
             )}
           </SidePanel>
         )}
       </AnimatePresence>
      </div>

      { }
      <AnimatePresence>
        {reportOpen && (
          <Modal title="Report" icon={<Spark size={18} animated={false} />} onClose={() => setReportOpen(false)}>
            <Markdown>{report}</Markdown>
          </Modal>
        )}
      </AnimatePresence>
    </div>
  )
}

type OutputTab =
  | { id: string; kind: 'artifact'; label: string; artifact: Artifact }
  | { id: 'deliverable'; kind: 'deliverable'; label: string }
  | { id: 'tasks'; kind: 'tasks'; label: string }

function OutputsPanel({ loop, artifacts, tasks, report, active, onOpenArtifact, onOpenTask, onExpandReport, onDownloadReport }: {
  loop: GoalLoop; artifacts: Artifact[]; tasks: TaskItem[]; report: string; active: boolean
  onOpenArtifact?: (slug: string) => void
  onOpenTask?: (taskId: string) => void
  onExpandReport: () => void; onDownloadReport: () => void
}) {
  const artifactTabs = (report ? artifacts.filter((a) => !a.slug.endsWith('-deliverable')) : artifacts)
    .map((a): OutputTab => ({ id: `a:${a.slug}`, kind: 'artifact', label: a.name, artifact: a }))
  const tabs: OutputTab[] = [
    ...(report ? [{ id: 'deliverable', kind: 'deliverable', label: 'Deliverable' } as OutputTab] : []),
    ...artifactTabs,
    ...(tasks.length ? [{ id: 'tasks', kind: 'tasks', label: `Tasks · ${tasks.length}` } as OutputTab] : []),
  ]
  const [activeId, setActiveId] = useState<string>('')
  const [copiedReport, setCopiedReport] = useState(false)
  const copyReport = () => {
    void copyText(report, 'the report').then((ok) => { if (ok) { setCopiedReport(true); setTimeout(() => setCopiedReport(false), 1500) } })
  }
  const current = tabs.find((t) => t.id === activeId) ?? tabs[0]
  useEffect(() => {
    if (tabs.length && !tabs.some((t) => t.id === activeId)) setActiveId(tabs[0].id)
  }, [tabs.map((t) => t.id).join(','), activeId])

  return (
    <div className="flex-1 min-h-0 rounded-xl bg-surface-container/50 flex flex-col overflow-hidden">
      <div className="shrink-0 px-l pt-m flex items-center gap-l border-b border-outline-variant/40">
        {tabs.length > 0 ? (
          <>
            {
}
            {
}
            <div role="tablist" aria-label="Loop views" onKeyDown={tabListKeys((i) => setActiveId(tabs[i].id))}
              className="flex-1 min-w-0 flex items-end gap-1 overflow-x-auto -mb-px">
              {tabs.map((t) => {
                const on = current?.id === t.id
                const Icon = t.kind === 'tasks' ? ListChecks : t.kind === 'deliverable' ? Spark : FileText
                return (
                  <button key={t.id} type="button" onClick={() => setActiveId(t.id)} role="tab" aria-selected={on}
                    tabIndex={on ? 0 : -1}
                    data-type="body-s" className={`shrink-0 inline-flex items-center gap-1.5 px-m h-9 max-w-[14rem] border-b-2 transition-colors ${on ? 'border-primary text-on-surface' : 'border-transparent text-on-surface-low hover:text-on-surface-var'}`}
                    style={on ? fvs(600) : undefined}
                    title={t.label}>
                    <Icon size={13} className="shrink-0" /><span className="truncate">{t.label}</span>
                  </button>
                )
              })}
            </div>
            { }
            {current?.kind === 'deliverable' && (
              <div className="shrink-0 flex items-center gap-1">
                <IconButton icon={copiedReport ? Check : Copy} label={copiedReport ? 'Copied' : 'Copy deliverable'} size={28} onClick={copyReport} />
                <IconButton icon={Download} label="Download deliverable" size={28} onClick={onDownloadReport} />
                <IconButton icon={Maximize2} label="Expand deliverable" size={28} onClick={onExpandReport} />
              </div>
            )}
            {current?.kind === 'artifact' && onOpenArtifact && (
              <IconButton icon={ExternalLink} label="Open in Artifacts" size={28} onClick={() => onOpenArtifact(current.artifact.slug)} />
            )}
          </>
        ) : (
          <Eyebrow as="span" className="h-9 inline-flex items-center">Outputs</Eyebrow>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-l py-l">
        {!current ? (
          <p data-type="body-s" className="text-on-surface-low">
            {active
              ? (loop.goal_type === 'verifiable'
                  ? 'This goal produces a passing check, not a document — outcomes will appear as the worker saves them.'
                  : (loop.total_cycles > 0
                      ? 'No outputs saved yet — they’ll appear here as the loop produces them.'
                      : 'Working on the first cycle… outputs appear here as the loop produces them.'))
              : 'No outputs yet.'}
          </p>
        ) : current.kind === 'deliverable' ? (
          <DeliverableDoc report={report} loop={loop} />
        ) : current.kind === 'tasks' ? (
          <div className="flex flex-col gap-1.5">
            {tasks.map((t) => {
              const done = t.status === 'done'
              const box = (
                <span className="shrink-0 inline-flex size-4 items-center justify-center rounded-sm border" style={{ borderColor: done ? 'var(--color-ok)' : 'var(--color-outline-variant)', background: done ? 'var(--color-ok)' : 'transparent' }}>{done && <Check size={11} className="text-on-primary" />}</span>
              )
              const label = <span className={`flex-1 min-w-0 truncate ${done ? 'text-on-surface-low line-through' : 'text-on-surface'}`}>{t.title}</span>
              const status = <span data-type="caption" className="shrink-0 text-on-surface-low">{t.status}</span>
              return onOpenTask ? (
                <button key={t.id} type="button" onClick={() => onOpenTask(t.id)}
                  data-type="body-s" className="group flex w-full items-center gap-s rounded-md px-2 py-1 -mx-2 text-left hover:bg-surface-2 transition-colors"
                  title="Open task">
                  {box}{label}{status}
                  <ChevronRight size={14} className="shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity" />
                </button>
              ) : (
                <div key={t.id} data-type="body-s" className="flex items-center gap-s">
                  {box}{label}{status}
                </div>
              )
            })}
          </div>
        ) : (
          <ArtifactTab artifact={current.artifact} onOpen={onOpenArtifact} loopId={loop.id} />
        )}
      </div>
    </div>
  )
}

function DocSurface({ eyebrow, children }: { eyebrow?: React.ReactNode; children: string }) {
  return (
    <div className="rounded-xl bg-surface px-2xl py-xl ring-1 ring-outline-variant/30" style={{ boxShadow: 'var(--shadow-composer)' }}>
      {eyebrow && <Eyebrow className="mb-l flex items-center gap-s border-b border-outline-variant/30 pb-m">{eyebrow}</Eyebrow>}
      <Markdown>{children}</Markdown>
    </div>
  )
}

function DeliverableDoc({ report, loop }: { report: string; loop: GoalLoop }) {
  const isGoal = (loop as { kind?: string }).kind === 'goal'
  return (
    <DocSurface eyebrow={<><span className="text-primary inline-flex"><Spark size={12} /></span> Deliverable{isGoal ? ` · ${GOAL_TYPE_LABEL[loop.goal_type] ?? loop.goal_type}` : ''}</>}>
      {report}
    </DocSurface>
  )
}

function ArtifactTab({ artifact, onOpen, loopId }: { artifact: Artifact; onOpen?: (slug: string) => void
  loopId?: string }) {
  const [content, setContent] = useState<string | null>(artifact.content ?? null)
  useEffect(() => {
    if (content != null) return
    let alive = true
    api.artifact(artifact.slug).then((full) => { if (alive) setContent(full.content ?? '') }).catch(() => { if (alive) setContent('') })
    return () => { alive = false }
  }, [artifact.slug])
  const ctype = useMemo(() => resolveContentType({ kind: artifact.kind }), [artifact.kind])

  const eyebrow = (
    <>
      <FileText size={12} className="text-primary" />
      { }
      <span className="normal-case tracking-normal">{artifact.kind}{artifact.version > 1 ? ` · v${artifact.version}` : ''}</span>
      {onOpen && <TextLink onClick={() => onOpen(artifact.slug)} icon={ExternalLink} iconPosition="trailing" iconSize={12} className="ml-auto normal-case tracking-normal">Open in Artifacts</TextLink>}
    </>
  )
  if (content == null) return <DocSurface eyebrow={eyebrow}>{'Loading…'}</DocSurface>
  if (!content.trim()) return (
    <DocSurface eyebrow={eyebrow}>{'_This artifact has no inline content — open it in Artifacts to view._'}</DocSurface>
  )
  if (ctype.id === 'markdown' || ctype.id === 'text') return <DocSurface eyebrow={eyebrow}>{content}</DocSurface>
  return (
    <div className="rounded-xl overflow-hidden bg-surface ring-1 ring-outline-variant/30" style={{ boxShadow: 'var(--shadow-composer)' }}>
      <Eyebrow className="flex items-center gap-s bg-surface px-l py-2 border-b border-outline-variant/30">{eyebrow}</Eyebrow>
      <div className="h-[60vh]">
        <ContentSurface type={ctype} content={content} title={artifact.name} docId={artifact.slug} readOnly
          iterate={loopId ? { slug: artifact.slug, correction: async (directive) => { await api.uLoopNudge(loopId, directive) } } : undefined} />
      </div>
    </div>
  )
}

function LiveSubsteps({ activity }: { activity: { kind: string; label: string; detail?: string }[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-1.5 pl-7">
      <button onClick={() => setOpen((v) => !v)} aria-expanded={open} data-type="caption" className="flex items-center gap-1.5 text-on-surface-low hover:text-on-surface">
        <Search size={12} /> {activity.length} steps <ChevronRight size={12} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
      </button>
      {open && (
        <ul className="mt-1 flex flex-col gap-1">
          {activity.slice(-12).map((e, i) => (
            <li key={i} data-type="caption" className="flex items-start gap-s text-on-surface-low">
              <span className="mt-1.5 size-1 shrink-0 rounded-pill" style={{ background: e.kind === 'tool' ? 'var(--color-secondary)' : 'var(--color-on-surface-low)' }} />
              <span className="truncate">{e.label}{e.detail ? ` · ${e.detail}` : ''}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function RailRow({ icon, label, hint, onClick }: {
  icon: React.ReactNode; label: string; hint?: string; onClick: () => void
}) {
  return (
    <button type="button" onClick={onClick}
      className="group w-full text-left rounded-lg px-m py-2.5 flex items-center gap-s hover:bg-surface-high transition-colors">
      <span className="shrink-0 text-primary">{icon}</span>
      <span data-type="label-s" className="flex-1 truncate text-on-surface" style={fvs(500)}>{label}</span>
      {hint && <span data-type="caption" className="shrink-0 text-on-surface-low">{hint}</span>}
      <ChevronRight size={15} className="shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity" />
    </button>
  )
}

function PhaseGroup({ phase, index, active, minCycles, cycles, renderCycle, liveCycle }: {
  phase: Record<string, unknown>; index: number; active: boolean; minCycles: number
  cycles: LoopFinding[]; renderCycle: (f: LoopFinding, idx: number) => React.ReactNode
  liveCycle?: React.ReactNode
}) {
  const [open, setOpen] = useState(active)
  const role = String(phase.role || '').trim()
  const target = String(phase.target || '').trim()
  const exit = String(phase.phase_exit || '').trim()
  const agent = String(phase.agent_name || '').trim()
  const skills = (phase.skill_ids as string[]) || []
  const wfs = (phase.workflow_ids as string[]) || []
  const orderedCycles = [...cycles].reverse()
  return (
    <div className={`rounded-lg ${active ? 'ring-1 ring-primary' : ''}`} style={{ background: 'color-mix(in srgb, var(--color-surface-container) 55%, transparent)' }}>
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="w-full flex items-center gap-s px-m py-2 text-left">
        <ChevronRight size={13} className={`shrink-0 text-on-surface-low transition-transform ${open ? 'rotate-90' : ''}`} />
        <span data-type="caption" className="shrink-0 inline-flex size-5 items-center justify-center rounded-pill bg-surface-high text-on-surface-low tabular-nums">{index + 1}</span>
        { }
        <span className="flex-1 min-w-0 flex flex-col">
          <span data-type="label-s" className="truncate text-on-surface" style={fvs(550)}>{role || `Phase ${index + 1}`}</span>
          <span data-type="caption" className="truncate text-on-surface-low">{agent ? <><Bot size={9} className="inline -mt-0.5 mr-0.5" />{agent}</> : 'default worker'}</span>
        </span>
        {active && <Eyebrow as="span" tone="primary" className="shrink-0 self-start">● active</Eyebrow>}
        {
}
        <span data-type="caption" className="shrink-0 text-on-surface-low tabular-nums" title={`${cycles.length} cycle${cycles.length !== 1 ? 's' : ''} run · minimum ${minCycles}`}>
          {cycles.length >= minCycles ? `${cycles.length} ${cycles.length === 1 ? 'cycle' : 'cycles'}` : `${cycles.length}/${minCycles}`}
        </span>
      </button>
      { }
      {open && (
        <div className="px-m pb-2 pl-[42px] flex flex-col gap-1">
          {target && <span data-type="body-s" className="text-on-surface-var">{target}</span>}
          {exit && <span data-type="caption" className="text-on-surface-low">↳ advances when: {exit}</span>}
          { }
          {(skills.length || wfs.length) ? (
            <div data-type="caption" className="flex flex-wrap items-center gap-1 mt-0.5">
              {skills.map((s) => <span key={s} className="inline-flex items-center rounded-pill px-1.5 h-5 bg-surface-high text-on-surface-low" title="Skill loaded this phase">{s}</span>)}
              {wfs.map((w) => <span key={w} className="inline-flex items-center rounded-pill px-1.5 h-5 bg-surface-high text-on-surface-low" title="Workflow loaded this phase">{w}</span>)}
            </div>
          ) : <span data-type="caption" className="text-on-surface-low">baseline capabilities only</span>}
        </div>
      )}
      { }
      <div className="px-m pb-2 flex flex-col gap-1">
        {liveCycle}
        {orderedCycles.map((f, i) => renderCycle(f, i))}
        {!liveCycle && orderedCycles.length === 0 && <p data-type="caption" className="text-on-surface-low">Not started.</p>}
      </div>
    </div>
  )
}

function CycleNode({ f, verdict, dur, hasNudge, onClick, delay }: { f: LoopFinding; verdict?: LoopVerdict; dur?: number; hasNudge: boolean; onClick: () => void; delay: number }) {
  return (
    <motion.button initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialFast, delay }} onClick={onClick}
      className="group w-full text-left rounded-lg bg-surface-container px-m py-m hover:bg-surface-high transition-colors">
      <div className="flex items-center gap-s">
        <span data-type="caption" className="shrink-0 inline-flex items-center justify-center size-5 rounded-pill tabular-nums" style={{ background: 'color-mix(in srgb, var(--color-primary) 20%, transparent)', color: 'var(--color-on-surface)' }}>{f.cycle}</span>
        <span data-type="label-s" className="flex-1 truncate text-on-surface" style={fvs(500)}>{asText(f.key_insight) || asText(f.summary) || `Cycle ${f.cycle}`}</span>
        {hasNudge && <MessageSquarePlus size={13} className="text-info shrink-0" />}
        {typeof verdict?.marginal_value === 'number' && <span data-type="caption" className="shrink-0 text-on-surface-low tabular-nums" title="judge's marginal value (return this cycle)">▲{verdict.marginal_value.toFixed(1)}</span>}
        <ChevronRight size={15} className="shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity" />
      </div>
      {dur != null && dur > 0 && <div data-type="caption" className="mt-1 pl-7 text-on-surface-low tabular-nums">took {fmt(dur)}</div>}
    </motion.button>
  )
}

function CycleDetail({ f, verdict, nudges, activity }: { f: LoopFinding; verdict?: LoopVerdict; nudges: LoopNudge[]; activity: { kind: string; label: string; detail?: string }[] }) {
  return (
    <div className="flex flex-col gap-l">
      <div className="flex flex-wrap gap-s">
        {typeof verdict?.marginal_value === 'number' && typeof verdict?.quality_score === 'number' && <span data-type="body-s" className="inline-flex items-center gap-1.5 rounded-pill px-m h-7" style={{ background: `color-mix(in srgb, ${verdict.done ? 'var(--color-ok)' : 'var(--color-primary)'} 18%, transparent)`, color: verdict.done ? 'var(--color-ok)' : 'var(--color-primary)' }}>{verdict.done ? <Check size={13} /> : null} judge ▲{verdict.marginal_value.toFixed(1)} · ★{verdict.quality_score.toFixed(1)}</span>}
        {verdict?.adversarial && <span data-type="body-s" className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 bg-surface-high text-on-surface-var" title="A second, skeptical judge independently cross-checked this verdict (adversarial review)."><ShieldCheck size={13} className="text-on-surface-low" /> cross-checked</span>}
        {f.metric && typeof f.metric.value === 'number' && <span data-type="body-s" className="inline-flex items-center rounded-pill px-m h-7 bg-surface-high text-on-surface-var">{asText(f.metric.name) || 'metric'}: {f.metric.value}</span>}
        {typeof f.new_findings_count === 'number' && <span data-type="body-s" className="inline-flex items-center rounded-pill px-m h-7 bg-surface-high text-on-surface-var">{f.new_findings_count} new this cycle</span>}
      </div>
      {f.key_insight && <Section label="Key insight"><p data-type="body-m" className="text-on-surface leading-relaxed">{asText(f.key_insight)}</p></Section>}
      {f.summary && <Section label="Summary"><p data-type="body-s" className="text-on-surface-var leading-relaxed">{asText(f.summary)}</p></Section>}
      {f.evidence && <Section label="Evidence"><p data-type="body-s" className="text-on-surface-var leading-relaxed whitespace-pre-wrap">{asText(f.evidence)}</p></Section>}
      {f.files_touched?.length ? (
        <Section label="Files written">
          <div className="flex flex-col gap-1.5">
            {f.files_touched.map((s, i) => (
              <div key={`f${i}`} data-type="body-s" className="flex items-start gap-s text-on-surface">
                <FileText size={13} className="text-on-surface-low shrink-0 mt-0.5" />
                <span data-type="caption" className="font-mono break-words">{asText(s)}</span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}
      {(verdict?.done_reason || verdict?.evidence_refs?.length) ? (
        <Section label="Judge verdict">
          {verdict.done_reason ? <p data-type="body-s" className="text-on-surface-var">{asText(verdict.done_reason)}{typeof verdict.band_used === 'number' && <span className="text-on-surface-low"> · returns band {verdict.band_used.toFixed(1)}</span>}</p> : null}
          {
}
          {verdict.evidence_refs?.length ? (
            <div className="mt-1.5 flex flex-col gap-1">
              <span data-type="caption" className="text-on-surface-low">Observed independently</span>
              {verdict.evidence_refs.map((ref, i) => <span key={i} data-type="body-s" className="flex items-start gap-s text-on-surface"><Check size={13} className="text-ok shrink-0 mt-0.5" /><span data-type="caption" className="font-mono break-words">{asText(ref)}</span></span>)}
            </div>
          ) : null}
        </Section>
      ) : null}
      {(f.sources_checked?.length || f.sources_empty?.length) ? (
        <Section label="Sources">
          <div className="flex flex-col gap-1.5">
            {f.sources_checked?.map((s, i) => <div key={`c${i}`} data-type="body-s" className="flex items-start gap-s text-on-surface"><Check size={13} className="text-ok shrink-0 mt-0.5" /><span data-type="caption" className="font-mono break-words">{asText(s)}</span></div>)}
            {f.sources_empty?.map((s, i) => <div key={`e${i}`} data-type="body-s" className="flex items-start gap-s text-on-surface-low"><X size={13} className="shrink-0 mt-0.5" /><span data-type="caption" className="font-mono break-words">{asText(s)} <span className="opacity-60">(empty)</span></span></div>)}
          </div>
        </Section>
      ) : null}
      {nudges.length > 0 && (
        <Section label={`Nudges applied (${nudges.length})`}>
          <div className="flex flex-col gap-s">
            {nudges.map((n, i) => (
              <div key={i} className="flex gap-s">
                <CornerDownRight size={14} className="text-info shrink-0 mt-1" />
                <div className="flex-1 rounded-md px-m py-2" style={{ background: 'color-mix(in srgb, var(--color-info) 8%, transparent)' }}>
                  <Eyebrow tone="info" className="mb-1">sent cycle {n.sent_at_cycle} · applied cycle {n.applied_cycle}</Eyebrow>
                  <p data-type="body-s" className="text-on-surface leading-relaxed whitespace-pre-wrap">{n.text}</p>
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}
      {activity.length > 0 && (
        <Section label="Live activity">
          <div className="flex flex-col gap-1.5">
            {activity.slice(-20).map((e, i) => (
              <div key={i} data-type="body-s" className="flex items-start gap-s">
                <span className="mt-1.5 size-1 shrink-0 rounded-pill" style={{ background: e.kind === 'tool' ? 'var(--color-secondary)' : 'var(--color-on-surface-low)' }} />
                <span className="text-on-surface-var"><span className="text-on-surface">{e.label}</span>{e.detail ? ` · ${e.detail}` : ''}</span>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><Eyebrow className="mb-1.5">{label}</Eyebrow>{children}</div>
}

function groupNudges(nudges: LoopNudge[]) {
  const byCycle = new Map<number, LoopNudge[]>(); const pending: LoopNudge[] = []
  for (const n of nudges) {
    if (n.applied_cycle == null) pending.push(n)
    else { const arr = byCycle.get(n.applied_cycle) ?? []; arr.push(n); byCycle.set(n.applied_cycle, arr) }
  }
  return { byCycle, pending }
}
