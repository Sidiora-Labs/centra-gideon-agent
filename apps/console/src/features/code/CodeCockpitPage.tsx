import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { fvs, withWeight } from '../../shared/theme/fontWeight'
import { createPortal } from 'react-dom'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Code2, Play, Pause, Square, Trash2, Loader2, ListChecks, FolderTree,
  TerminalSquare, X, Send, CircleDot, CheckCircle2, Circle, Wrench, Activity, GitBranch,
  ChevronDown, ChevronRight, ChevronLeft, Target, FileCode, HelpCircle, Folder, Repeat, Clock, XCircle, AlertTriangle,
  PanelLeftClose, PanelRightClose, PanelLeftOpen, PanelRightOpen, FilePlus2, FolderPlus, Rocket, Hand, Plus, RotateCcw, FolderKanban, CirclePlay,
} from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { Button } from '../../shared/ui/Button'
import { TextLink } from '../../shared/ui/TextLink'
import { Eyebrow } from '../../shared/ui/Eyebrow'
import { IconButton } from '../../shared/ui/IconButton'
import { LoadError } from '../../shared/ui/ListScaffold'
import { FieldError } from '../../shared/ui/forms'
import { Centered } from '../../shared/ui/Centered'
import { UnifiedDiff } from '../../shared/ui/UnifiedDiff'
import { confirm, confirmDelete } from '../../shared/ui/dialog'
import { api, type CodeProject, type CodeStage, type CodeFinding, type FsEntry, type TaskItem, type Loop } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { useChatSocket, type WsMessage } from '../../shared/data/useChatSocket'
import { useVisiblePoll } from '../../shared/data/useVisiblePoll'
import { cleanSay, toolDetail } from '../../shared/data/agentFeed'
import { ACTIVE_LOOP_STATUSES, effectiveLoopStatus, LOOP_ACTION_SOURCE_STATUSES } from '../../shared/data/loopStatus'
import { useRunStream } from '../loops/useRunStream'
import { belongsToLoop } from '../workflows/containerKey'
import { foldReducer, emptyRunFlags, type RunFlags } from '../loops/runFold'
import { SearchField } from '../../shared/ui/SearchField'
import { DiffView } from './DiffView'
import { WorkspacePicker } from './WorkspacePicker'
import { FileTree } from '../files/browse/FileTree'
import { FileViewer, type FileViewerHandle } from '../files/browse/FileViewer'
import { useFileTabs } from '../files/browse/useFileTabs'
import { useDirCache, useGitStatus } from '../files/filesData'
import { TerminalView } from '../terminal/TerminalView'
import { runInTerminalWhenReady } from '../terminal/terminalBridge'
import type { TermTab } from '../terminal/TerminalPage'
import { TypingReveal } from './TypingReveal'
import { DiffReveal } from './DiffReveal'
import { codeDeleteBody } from './codeMeta'
import { useResizablePanel } from '../../shared/ui/useResizablePanel'
import { CockpitPromptBar } from '../loops/CockpitPromptBar'
import { useMode } from '../../app/shell/theme'
import { useQueryFlag, type RouteProps } from '../../app/shell/useQueryState'
import { overlayEnter, messageEnter, listItemEnter, stagger, physics } from '../../shared/theme/motion'
import { Expandable } from '../../shared/ui/motion'
import { BUSY_REASON } from '../../shared/ui/unavailable'

const EMPTY_ARTIFACTS = new Set<string>()

function loopToCodeProject(p: Loop): CodeProject {
  const kc = (p.kind_config || {}) as Record<string, unknown>
  return {
    ...(p as unknown as CodeProject),
    entry_stage: (kc.entry_stage as CodeProject['entry_stage']) ?? 'ideation',
    project_kind: (kc.project_kind as CodeProject['project_kind']) ?? 'greenfield',
    verify_command: String(kc.verify_command ?? ''),
    test_command: String(kc.test_command ?? ''),
    queued_task_ids: Array.isArray(kc.queued_task_ids) ? (kc.queued_task_ids as string[]) : [],
    stage_plan: (p.plan ?? []) as unknown as CodeStage[],
    stage_status: (p.phase_status ?? {}) as Record<string, string>,
  }
}

const STEERABLE = new Set([...ACTIVE_LOOP_STATUSES, 'failed'])
const TERMINAL_STATUSES = new Set(['complete', 'stopped'])
const REVEAL_MAX_CHARS = 40_000

export function resolveTouchedPath(raw: string, root: string): { abs: string; rel: string } | null {
  if (typeof raw !== 'string' || !raw || !root) return null
  let p = raw.trim()
    .replace(/\s+[-–—→:]\s+.*$/, '')
    .replace(/\s+\([^)]*\)\s*$/, '')
    .replace(/:\d+(?::\d+)?$/, '')
  if (/[\u0000-\u001f]/.test(p) || p.split('/').includes('..')) return null
  const mk = '/.gideon-worktrees/'
  const i = p.indexOf(mk)
  if (i >= 0) {
    const rel = p.slice(i + mk.length).split('/').slice(1).join('/')
    p = rel ? `${root}/${rel}` : p
  }
  if (p.includes(mk)) return null
  if (p.startsWith(root + '/')) return { abs: p, rel: p.slice(root.length + 1) }
  if (p.startsWith('/')) {
    const wsBase = root.split('/').pop() || ''
    const marker = `/${wsBase}/`
    const mi = wsBase ? p.lastIndexOf(marker) : -1
    if (mi >= 0) { const rel = p.slice(mi + marker.length); return { abs: `${root}/${rel}`, rel } }
    return null
  }
  const rel = p.replace(/^\.?\//, '')
  return rel ? { abs: `${root}/${rel}`, rel } : null
}

const stageKey = (s: CodeStage): string => (s.stage || s.title || '')

export function normalizeStageLabel(label: string): string {
  return (label || '')
    .replace(/^\s*(?:stage\s*)?\d+\s*\/\s*\d+\s*[.–—:)\-]?\s*/i, '')
    .replace(/^\s*(?:stage\s*)?\d+\s*[.–—:)\-]\s*/i, '')
    .trim().toLowerCase()
}

const cmdLabel = (cmd: string, max = 36): string => {
  const c = (cmd || '').trim()
  return c.length > max ? `${c.slice(0, max - 1)}…` : c
}

function autoGrowTextarea(el: HTMLTextAreaElement | null) {
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

interface ActivityItem { kind: 'tool' | 'status' | 'say'; label: string; detail?: string; rawLabel?: string }

export function CodeCockpitPage({ id, onBack, onDeleted, onNewTarget, onOpenProject, onStartNew, query, setQuery }: {
  id: string
  onBack: () => void
  onDeleted: () => void
  onNewTarget?: (workspaceDir: string) => void
  onOpenProject?: (projectId: string) => void
  onStartNew?: () => void
} & Pick<RouteProps, 'query' | 'setQuery'>) {
  const [project, setProject] = useState<CodeProject | null | 'missing'>(null)
  const [showTerm, setShowTerm] = useQueryFlag(query, setQuery, 'term')
  const [pendingRunCmd, setPendingRunCmd] = useState<{ cmd: string; n: number } | null>(null)
  const pendingRunNonce = useRef(0)
  const [pickWs, setPickWs] = useState(false)
  const [acting, setActing] = useState(false)
  const [wsMissing, setWsMissing] = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const cancelRename = useRef(false)
  const renameInFlight = useRef(false)

  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const load = useCallback(() => {
    api.uLoop(id).then((proj) => { setProject(loopToCodeProject(proj)); setLoadErr(null) }).catch((e) => {
      const status = (e as { status?: number })?.status
      if (status === 404 || status === 400) setProject('missing')
      else setLoadErr(new Error((e as Error).message || 'Could not load this project'))
    })
  }, [id])
  useEffect(() => { load() }, [load])
  useVisiblePoll(() => load(), project === null && loadErr ? 4000 : null)
  useEffect(() => {
    const p = project
    if (!p || p === 'missing') return
    const w = (p.workspace_dir || '').trim()
    if (!w || p.project_kind !== 'brownfield' || p.status === 'running') { setWsMissing(false); return }
    let alive = true
    api.browseDirs(w).then(() => { if (alive) setWsMissing(false) })
      .catch((e) => {
        if (!alive) return
        const msg = (e instanceof Error ? e.message : '').toLowerCase()
        setWsMissing(msg.includes('no such directory'))
      })
    return () => { alive = false }
  }, [project])

  const { data: cachedProject } = useQuery(`code:project:${id}`, () => api.uLoop(id).then(loopToCodeProject).catch(() => null), { persist: false })
  useEffect(() => {
    if (project === null && cachedProject) setProject(cachedProject)
  }, [cachedProject, project])

  const [runFlags, setRunFlags] = useState<RunFlags>(emptyRunFlags)
  const gateFail = runFlags.gate
  const stalled = runFlags.stall
  const [toast, setToast] = useState<{ kind: 'question' | 'conflict' | 'error' | 'ok'; text: string } | null>(null)
  const [tasksNonce, setTasksNonce] = useState(0)

  const followRef = useRef(true)

  const { connected } = useRunStream(id, project !== 'missing', {
    onSnapshot: (p) => setProject(loopToCodeProject(p)),
    onLifecycle: (event, data) => {
      setRunFlags((f) => foldReducer(f, event, data))
      if (event === 'deleted') { setProject('missing'); return }
      if (event === 'gate_check') {
        const d = (data ?? {}) as { ok?: boolean; label?: string; output?: string }
        if (d.ok === false && d.label === 'merge') setToast({ kind: 'conflict', text: d.output || 'A merge conflict needs your attention.' })
        return
      }
      if (event === 'stage_stalled') return
      if (event === 'task_done') {
        const tid = (data as { task_id?: string } | null)?.task_id
        if (tid) setActivityBySession((m0) => {
          const k = `loop-${id}-${tid}`
          if (!(k in m0)) return m0
          const { [k]: _drop, ...rest } = m0
          return rest
        })
      }
      if (event === 'needs_input') {
        setToast({ kind: 'question', text: 'The worker has a question and is waiting for your answer.' })
      } else {
        setToast(null)
      }
      load()
      setTasksNonce((n) => n + 1)

      if (event === 'new_finding' && followRef.current) {
        const f = (data as { finding?: CodeFinding } | null)?.finding
        const wsRoot = (project !== 'missing' && (project?.workspace_dir || project?.files_dir) || '').replace(/\/$/, '')
        const isProjDir = project !== 'missing' && !project?.workspace_dir
        const touched = (f?.files_touched ?? [])
          .map((raw) => resolveTouchedPath(raw, wsRoot))
          .filter((r): r is { abs: string; rel: string } => {
            if (!r) return false
            if (!isProjDir) return true
            const top = r.rel.split('/')[0]
            return !PROJECT_DIR_HIDDEN.has(top) && ![...PROJECT_DIR_HIDDEN_PREFIXES].some((pre) => top.startsWith(pre))
          })
          .map((r) => r.abs)
        const latest = touched[touched.length - 1]
        if (latest) window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: { name: latest.split('/').pop(), path: latest, is_dir: false, _follow: true } }))
      }
    },
  })
  useEffect(() => {
    const onManual = (e: Event) => { if (!(e as CustomEvent).detail?._follow) followRef.current = false }
    const onEditing = () => { followRef.current = false }
    const onReFollow = () => { followRef.current = true }
    const onToast = (e: Event) => {
      const d = (e as CustomEvent).detail as { kind?: 'ok' | 'error'; text?: string } | null
      if (d?.text) setToast({ kind: d.kind === 'error' ? 'error' : 'ok', text: d.text })
    }
    window.addEventListener('ne:code-open-file', onManual as EventListener)
    window.addEventListener('ne:code-open-diff', onManual as EventListener)
    window.addEventListener('ne:code-open-commit', onManual as EventListener)
    window.addEventListener('ne:code-editing', onEditing as EventListener)
    window.addEventListener('ne:code-follow-worker', onReFollow as EventListener)
    window.addEventListener('ne:code-toast', onToast as EventListener)
    return () => {
      window.removeEventListener('ne:code-open-file', onManual as EventListener)
      window.removeEventListener('ne:code-open-diff', onManual as EventListener)
      window.removeEventListener('ne:code-open-commit', onManual as EventListener)
      window.removeEventListener('ne:code-editing', onEditing as EventListener)
      window.removeEventListener('ne:code-follow-worker', onReFollow as EventListener)
      window.removeEventListener('ne:code-toast', onToast as EventListener)
    }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'p') {
        if ((document.activeElement as HTMLElement | null)?.closest('.xterm')) return
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('ne:code-expand-panel', { detail: 'code-left' }))
        setTimeout(() => window.dispatchEvent(new CustomEvent('ne:code-focus-find')), 0)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const isRunning = project !== null && project !== 'missing' && project.status === 'running'
  const pollTick = useRef(0)
  useVisiblePoll(() => {
    setTasksNonce((n) => n + 1)
    if (++pollTick.current % 3 === 0) load()
  }, isRunning ? 8000 : null)

  const [activityBySession, setActivityBySession] = useState<Record<string, ActivityItem[]>>({})
  const workerKey = `loop-${id}`
  const onWs = useCallback((m: WsMessage) => {
    const sess = String(m.data?.session ?? '')
    if (!belongsToLoop(sess, id)) return
    const push = (item: ActivityItem) => setActivityBySession((m0) => ({ ...m0, [sess]: [...(m0[sess] ?? []), item].slice(-50) }))
    const pushStatus = (label: string) => setActivityBySession((m0) => {
      const a = m0[sess] ?? []
      const last = a[a.length - 1]
      if (last && last.kind === 'status' && last.label === label) return m0
      return { ...m0, [sess]: [...a, { kind: 'status' as const, label }].slice(-50) }
    })
    if (m.type === 'chat_status') {
      const sgi = String(m.data.status ?? ''); if (sgi) pushStatus(sgi)
    } else if (m.type === 'tool_call') {
      const detail = toolDetail(String(m.data.input_preview ?? ''), String(m.data.purpose ?? ''))
      push({ kind: 'tool', label: String(m.data.tool ?? 'tool'), detail })
    } else if (m.type === 'chat_chunk') {
      const piece = String(m.data.content ?? '')
      if (piece) setActivityBySession((m0) => {
        const a = m0[sess] ?? []
        const last = a[a.length - 1]
        if (last && last.kind === 'say') {
          const raw = ((last.rawLabel ?? last.label) + piece).slice(-4000)
          return { ...m0, [sess]: [...a.slice(0, -1), { ...last, rawLabel: raw, label: cleanSay(raw) }] }
        }
        return { ...m0, [sess]: [...a, { kind: 'say' as const, label: cleanSay(piece), rawLabel: piece }].slice(-50) }
      })
    } else if (m.type === 'activity_event') {
      const text = String(m.data.text ?? ''); if (text) pushStatus(text)
    } else if (m.type === 'chat_done') {
      setActivityBySession((m0) => ({ ...m0, [sess]: [] }))
    }
  }, [workerKey])
  useChatSocket(onWs)

  const refetchTasks = useCallback(() => { load(); setTasksNonce((n) => n + 1) }, [load])

  if (project === null) {
    return <Shell title="Project" onBack={onBack}><Centered>
      {loadErr ? (
        <LoadError what="project" error={loadErr} onRetry={load} />
      ) : (
        <Loader2 size={22} className="animate-spin text-on-surface-low" />
      )}
    </Centered></Shell>
  }
  if (project === 'missing') {
    return <Shell title="Project" onBack={onBack}><Centered>
      <div className="flex max-w-[360px] flex-col items-center gap-3 px-6 text-center">
        <HelpCircle size={32} className="text-on-surface-low" />
        <div>
          <p data-type="title-m" className="text-on-surface">This project no longer exists</p>
          <p data-type="body-s" className="mt-1 text-on-surface-low">It may have been deleted, or the link is stale.</p>
        </div>
        <Button onClick={onBack}><ListChecks size={15} /> Back to projects</Button>
      </div>
    </Centered></Shell>
  }

  const p = project
  const ws = p.workspace_dir || ''
  const fileRoot = ws || p.files_dir || ''
  const active = p.status === 'running'

  async function act(action: 'start' | 'pause' | 'resume' | 'stop') {
    if (acting) return

    if (action === 'start' && p.project_kind === 'brownfield' && !ws) { setPickWs(true); return }
    setActing(true)
    try { setProject(loopToCodeProject(await api.uLoopAction(id, action))); setToast(null) }
    catch (e) {
      setToast({ kind: 'error', text: `Couldn't ${action} this project: ${(e as Error).message || 'unknown error'}` })
      load()
    } finally { setActing(false) }
  }
  function runInWorkspaceTerminal(command: string) {
    const cmd = (command || '').trim()
    if (!cmd) return
    setShowTerm(true)
    setPendingRunCmd({ cmd, n: pendingRunNonce.current++ })
  }
  async function pickWorkspace(dir: string) {
    if (acting) return
    setPickWs(false)
    setActing(true)
    try {
      await api.updateULoop(id, { workspace_dir: dir })
      setProject(loopToCodeProject(await api.uLoopAction(id, 'start'))); setToast(null)
    } catch (e) {
      setToast({ kind: 'error', text: `Couldn't start with that folder: ${(e as Error).message || 'unknown error'}` })
      load()
    } finally { setActing(false) }
  }
  async function del() {
    if (!(await confirmDelete('project', p.name, { body: codeDeleteBody(p) }))) return
    try { await api.deleteULoop(id); onDeleted() }
    catch (e) { setToast({ kind: 'error', text: `Couldn't delete this project: ${(e as Error).message || 'unknown error'}` }) }
  }
  function startRename() { cancelRename.current = false; setTitleDraft(p.name || ''); setEditingTitle(true) }
  function abortRename() { cancelRename.current = true; setEditingTitle(false) }
  async function commitRename() {
    setEditingTitle(false)
    if (cancelRename.current) { cancelRename.current = false; return }
    if (renameInFlight.current) return
    const name = titleDraft.trim()
    if (!name || name === (p.name || '')) return
    renameInFlight.current = true
    try { setProject(loopToCodeProject(await api.updateULoop(id, { name }))) }
    catch (e) { setToast({ kind: 'error', text: `Couldn't rename: ${(e as Error).message || 'unknown error'}` }) }
    finally { renameInFlight.current = false }
  }

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar
        left={<div className="flex min-w-0 items-center gap-2">
          {
}
          {onOpenProject && (p.tasks_project_id || p.project_id) && (
            <IconButton icon={ChevronLeft} label="Back to the project this loop belongs to"
              onClick={() => onOpenProject((p.tasks_project_id || p.project_id) as string)}
              size={28} iconSize={18} className="shrink-0" />
          )}
          <Code2 size={18} className="shrink-0 text-primary" />
          {editingTitle ? (
            <input autoFocus value={titleDraft} onChange={(e) => setTitleDraft(e.target.value)}
              onBlur={commitRename} onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); else if (e.key === 'Escape') abortRename() }}
              aria-label="Rename project"
              data-type="body-m" className="min-w-[14rem] h-7 rounded-md bg-surface-high px-2 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
          ) : (
            <button type="button" onClick={startRename} title="Rename project"
              data-type="title-l" className="truncate text-on-surface text-left hover:text-on-surface-var">
              {p.name}
            </button>
          )}
          {
}
          {
}
          {active && (
            <span data-type="caption" className="inline-flex shrink-0 items-center gap-1 text-on-surface-low">
              <span className="inline-block size-1.5 rounded-pill"
                style={{ background: connected ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }} />
              {connected ? 'Streaming' : 'Connecting…'}
            </span>
          )}
        </div>}
        right={<HeaderActions>
          {
}
          {LOOP_ACTION_SOURCE_STATUSES.pause.has(p.status)
            ? <HeaderControl icon={Pause} label="Pause" priority="primary" onClick={() => act('pause')} disabled={acting} />
            : LOOP_ACTION_SOURCE_STATUSES.resume.has(p.status)
              ? <HeaderControl icon={Play} label="Resume" priority="primary" onClick={() => act('resume')} variant="primary" disabled={acting} />
              : LOOP_ACTION_SOURCE_STATUSES.start.has(p.status)
                ? <HeaderControl icon={Play} label="Start" priority="primary" onClick={() => act('start')} variant="primary" disabled={acting} />
                : null}
          {
}
          {LOOP_ACTION_SOURCE_STATUSES.stop.has(p.status) &&
            <HeaderControl icon={Square} label="Stop" priority="primary" onClick={async () => {
              if (await confirm({ title: 'Stop this run?', body: "Stopping ends the project for good — it can't be resumed afterward (you'd start a new one). Work already merged into your workspace is kept, but a task still running loses its own worktree and branch. Pause instead if you just want to step in.", danger: true, confirmLabel: 'Stop' })) act('stop')
            }} />}
          { }
          {!!ws && <HeaderControl icon={TerminalSquare} label={showTerm ? 'Hide terminal' : 'Terminal'} onClick={() => setShowTerm(!showTerm)} active={showTerm} />}
          {
}
          {!!ws && p.verify_command && <HeaderControl icon={Play} label={`Run build (${cmdLabel(p.verify_command)})`} priority="low" onClick={() => runInWorkspaceTerminal(p.verify_command || '')} />}
          {!!ws && p.test_command && <HeaderControl icon={Play} label={`Run tests (${cmdLabel(p.test_command)})`} priority="low" onClick={() => runInWorkspaceTerminal(p.test_command || '')} />}
          {
}
          {onNewTarget && !active && !!ws && <HeaderControl icon={Target} label="New target" priority="low" onClick={() => onNewTarget(ws)} />}
          <HeaderControl icon={ListChecks} label="All projects" priority="low" onClick={onBack} />
          <HeaderControl icon={Trash2} label="Delete project" danger priority="low" onClick={() => { void del() }} />
        </HeaderActions>} />

      <CockpitMeta project={p} onOpenProject={onOpenProject} />

      { }
      <CockpitPromptBar prompt={p.task || ''} />

      {
}
      {p.status === 'ready' && p.project_kind === 'brownfield' && !ws && (
        <motion.div variants={messageEnter} initial="initial" animate="animate"
          data-type="body-s" className="flex shrink-0 items-center justify-between gap-2 border-b border-outline-variant/40 bg-warn/10 px-l py-2"
          style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)', color: 'var(--color-warn)' }}>
          <span>This brownfield project needs a workspace directory before it can start.</span>
          <Button variant="ghost" size="xs" onClick={() => setPickWs(true)} className="shrink-0">Choose folder</Button>
        </motion.div>
      )}
      {
}
      {ws && wsMissing && p.status !== 'running' && !TERMINAL_STATUSES.has(p.status) && (
        (p.status === 'ready' || p.status === 'review') ? (
          <motion.div variants={messageEnter} initial="initial" animate="animate"
            data-type="body-s" className="flex shrink-0 items-center justify-between gap-2 border-b border-outline-variant/40 px-l py-2"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)', color: 'var(--color-warn)' }}>
            <span>The workspace folder <span className="font-mono">{ws.split('/').slice(-1)[0]}</span> no longer exists — re-pick it to continue.</span>
            <Button variant="ghost" size="xs" onClick={() => setPickWs(true)} className="shrink-0">Re-pick folder</Button>
          </motion.div>
        ) : (
          <motion.div variants={messageEnter} initial="initial" animate="animate"
            data-type="body-s" className="flex shrink-0 items-center gap-2 border-b border-outline-variant/40 px-l py-2"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)', color: 'var(--color-warn)' }}>
            <span>The workspace folder <span className="font-mono">{ws.split('/').slice(-1)[0]}</span> no longer exists, so this run can't continue. Its files are gone — Stop or Delete the project, or restore the folder and reopen.</span>
          </motion.div>
        )
      )}

      {
}
      <div className="flex min-h-0 flex-1">
        <CollapsiblePanel side="left" panelKey="code-left" def={300} min={200} max={460}
          icon={FolderTree} label="Files">
          <FilesRail ws={fileRoot} isProjectDir={!ws} running={active} />
        </CollapsiblePanel>
        <CenterEditor ws={fileRoot} showTerm={showTerm} onCloseTerm={() => setShowTerm(false)} running={active} runCmd={pendingRunCmd} />
        <CollapsiblePanel side="right" panelKey="code-right" def={340} min={260} max={520}
          icon={ListChecks} label="Tasks">
          <RightPanel project={p} onTasksChanged={refetchTasks} tasksNonce={tasksNonce}
            activityBySession={activityBySession} gateFail={gateFail} stalled={stalled} onNudged={load} onStartNew={onStartNew} />
        </CollapsiblePanel>
      </div>

      {pickWs && (
        <WorkspacePicker mode="brownfield" onClose={() => setPickWs(false)} onPick={pickWorkspace} />
      )}
      <AnimatePresence>
        {toast && (
          <CodeToast kind={toast.kind} text={toast.text} onDismiss={() => setToast(null)}
            onRespond={(toast.kind === 'error' || toast.kind === 'ok') ? undefined : () => { setToast(null); window.dispatchEvent(new CustomEvent('ne:code-focus-steer')) }} />
        )}
      </AnimatePresence>
    </div>
  )
}

function CodeToast({ kind, text, onDismiss, onRespond }: {
  kind: 'question' | 'conflict' | 'error' | 'ok'; text: string; onDismiss: () => void; onRespond?: () => void
}) {
  const dismissRef = useRef(onDismiss); dismissRef.current = onDismiss
  useEffect(() => {
    if (kind !== 'ok') return
    const t = setTimeout(() => dismissRef.current(), 4000)
    return () => clearTimeout(t)
  }, [kind, text])
  const tone = kind === 'error' ? 'var(--color-danger)'
    : kind === 'conflict' ? 'var(--color-warn)' : kind === 'ok' ? 'var(--color-ok)' : 'var(--color-info)'
  const Icon = kind === 'error' ? XCircle : kind === 'conflict' ? AlertTriangle : kind === 'ok' ? CheckCircle2 : HelpCircle
  return createPortal(
    <motion.div role="alert" aria-live="assertive"
      variants={overlayEnter} initial="initial" animate="animate" exit="exit"
      className="fixed bottom-4 right-4 z-[var(--z-toast)] w-[360px] max-w-[calc(100vw-2rem)] rounded-xl border border-outline-variant/50 bg-surface-container p-3.5 shadow-lg">
      <div className="flex items-start gap-2.5">
        <Icon size={18} className="mt-0.5 shrink-0" style={{ color: tone }} />
        <div className="min-w-0 flex-1">
          <p data-type="label-s" className="text-on-surface" style={fvs(600)}>
            {kind === 'error' ? "That didn't work" : kind === 'conflict' ? 'Merge conflict — needs you' : kind === 'ok' ? 'Done' : 'The worker needs your input'}
          </p>
          <p data-type="caption" className="mt-0.5 text-on-surface-var">{text}</p>
          <div className="mt-2 flex items-center gap-2">
            {
}
            {onRespond && <button type="button" onClick={onRespond}
              data-type="caption" className="rounded-md px-2.5 py-1" style={{ background: tone, color: 'var(--color-on-primary)' }}>Respond</button>}
            <Button variant="ghost" size="xs" onClick={onDismiss}>Dismiss</Button>
          </div>
        </div>
        <IconButton icon={X} label="Dismiss" onClick={onDismiss} size={24} iconSize={14} className="shrink-0" />
      </div>
    </motion.div>,
    document.body,
  )
}


function StageTrail({ project }: { project: CodeProject }) {
  const plan = project.stage_plan ?? []
  if (!plan.length) return null
  const running = project.status === 'running'
  const started = !['ready', 'review', 'intake', 'planning'].includes(project.status)
  const activeIdx = started
    ? plan.findIndex((s) => (project.stage_status?.[stageKey(s)] ?? 'pending') !== 'done')
    : -1
  const allDone = started && activeIdx < 0
  const cur = allDone || !started ? null : plan[activeIdx]
  const ATTENTION = ['blocked', 'needs_input', 'stagnant', 'failed', 'stopped']
  const halted = ATTENTION.includes(project.status)
  return (
    <>
      {
}
      <div className="ml-1 hidden min-w-0 items-center gap-1 overflow-hidden md:flex">
        {plan.map((s, i) => {
          const st = i < activeIdx || allDone ? 'done' : i === activeIdx ? 'active' : 'pending'
          const color = st === 'done' ? 'var(--color-ok)'
            : st === 'active' ? (halted ? 'var(--color-warn)' : 'var(--color-primary)')
            : 'var(--color-on-surface-low)'
          const activeRunning = st === 'active' && running
          const Icon = st === 'done' ? CheckCircle2 : activeRunning ? Loader2 : st === 'active' ? CircleDot : Circle
          return (
            <span key={i} className="inline-flex min-w-0 items-center gap-1" title={`${s.title || s.stage} — ${st}`}>
              {i > 0 && <span className="shrink-0 text-on-surface-low/40">›</span>}
              <Icon size={12} className={`shrink-0 ${activeRunning ? 'animate-spin' : ''}`} style={{ color }} />
              <span data-type="caption" className="max-w-[7rem] truncate" style={{ color }}>{s.title || s.stage}</span>
            </span>
          )
        })}
      </div>
      {
}
      <span data-type="caption" className="ml-1 inline-flex items-center gap-1 md:hidden"
        style={{ color: allDone ? 'var(--color-ok)' : halted ? 'var(--color-warn)' : started ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }}>
        {!started
          ? <><Circle size={12} /> {plan.length} stage{plan.length === 1 ? '' : 's'} planned</>
          : allDone
          ? <><CheckCircle2 size={12} /> all stages done</>
          : <>{running ? <Loader2 size={12} className="animate-spin" /> : <CircleDot size={12} />} stage {activeIdx + 1}/{plan.length} · {cur?.title || cur?.stage}</>}
      </span>
    </>
  )
}

function CockpitMeta({ project: p, onOpenProject }: { project: CodeProject; onOpenProject?: (projectId: string) => void }) {
  const ws = p.workspace_dir || ''
  const cycles = p.total_cycles || 0
  const projId = p.tasks_project_id || p.project_id || ''
  const [projName, setProjName] = useState('')
  useEffect(() => {
    if (!projId) { setProjName(''); return }
    let alive = true
    api.project(projId).then((pr) => { if (alive) setProjName(pr.name) }).catch(() => {})
    return () => { alive = false }
  }, [projId])
  const [, tick] = useState(0)
  useVisiblePoll(() => tick((n) => n + 1), p.status === 'running' ? 30000 : null)
  const banked = p.elapsed_seconds || 0
  const liveStretch = p.status === 'running' && p.started_at ? Math.max(0, Date.now() / 1000 - p.started_at) : 0
  const elapsed = banked + liveStretch
  const wsBase = ws ? (ws.replace(/\/$/, '').split('/').pop() || ws) : ''
  const cap = p.max_cycles || 0
  const cyclesText = (cycles > 0 || cap > 0)
    ? (cap > 0 ? `${cycles} / ${cap} cycles` : `${cycles} cycle${cycles === 1 ? '' : 's'}`)
    : ''
  const elapsedText = elapsed > 0 ? fmtDuration(elapsed) : ''
  const showProj = !!(projId && projName)
  if (!wsBase && !showProj && !cyclesText && !elapsedText) return null
  return (
    <div data-type="caption" className="flex shrink-0 items-center gap-3 border-b border-outline-variant/40 bg-surface-low/30 px-l py-1 text-on-surface-low">
      {
}
      {elapsedText && (
        <span className="inline-flex shrink-0 items-center gap-1" title="Elapsed run time">
          <Clock size={11} className="shrink-0 opacity-70" />
          <span className="font-mono">{elapsedText}</span>
        </span>
      )}
      {
}
      <StageTrail project={p} />
      {
}
      {(wsBase || showProj || cyclesText) && (
        <span className="ml-auto inline-flex min-w-0 shrink-0 items-center gap-3">
          {wsBase && (
            <span className="inline-flex shrink-0 items-center gap-1" title={ws}>
              <Folder size={11} className="shrink-0 opacity-70" />
              <span className="max-w-[220px] truncate font-mono">{wsBase}</span>
            </span>
          )}
          {showProj && (
            onOpenProject ? (
              <button type="button" onClick={() => onOpenProject(projId)} title={`Project: ${projName} — open`}
                className="inline-flex min-w-0 items-center gap-1 text-on-surface-var hover:text-primary">
                <FolderKanban size={11} className="shrink-0 text-primary" />
                <span className="max-w-[180px] truncate">{projName}</span>
              </button>
            ) : (
              <span className="inline-flex min-w-0 items-center gap-1" title={`Project: ${projName}`}>
                <FolderKanban size={11} className="shrink-0 text-primary" />
                <span className="max-w-[180px] truncate">{projName}</span>
              </span>
            )
          )}
          {cyclesText && (
            <span className="inline-flex shrink-0 items-center gap-1"
              title={cap > 0 ? `${cycles} of a ${cap}-cycle budget run` : `${cycles} cycles run (uncapped)`}>
              <Repeat size={11} className="shrink-0 opacity-70" />
              <span className="font-mono">{cyclesText}</span>
            </span>
          )}
        </span>
      )}
      {
}
    </div>
  )
}

function fmtDuration(secs: number): string {
  const s = Math.floor(secs)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ${m % 60}m`
  const d = Math.floor(h / 24)
  return `${d}d ${h % 24}h`
}


function CollapsiblePanel({ side, panelKey, def, min, max, icon: Icon, label, children }: {
  side: 'left' | 'right'; panelKey: string; def: number; min: number; max: number
  icon: typeof ListChecks; label: string; children: React.ReactNode
}) {
  const { width, collapsed, setCollapsed, onHandleDown, onHandleKey } = useResizablePanel(panelKey, { def, min, max, side, collapsible: true })
  useEffect(() => {
    const onExpand = (e: Event) => { if ((e as CustomEvent).detail === panelKey) setCollapsed(false) }
    window.addEventListener('ne:code-expand-panel', onExpand as EventListener)
    return () => window.removeEventListener('ne:code-expand-panel', onExpand as EventListener)
  }, [panelKey, setCollapsed])
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('ne:code-panel-collapsed', { detail: { panelKey, side, label, collapsed } }))
  }, [panelKey, side, label, collapsed])
  const borderSide = side === 'left' ? 'border-r' : 'border-l'
  if (collapsed) return null
  const handle = (
    <div onPointerDown={onHandleDown} onKeyDown={onHandleKey} role="separator" aria-orientation="vertical"
      tabIndex={0} aria-label={`Resize ${label} — arrow keys to resize`}
      aria-valuenow={Math.round(width)} aria-valuemin={min} aria-valuemax={max}
      className="group/handle absolute top-0 bottom-0 z-20 w-2 cursor-col-resize outline-none focus-visible:bg-primary/30"
      style={{ [side === 'left' ? 'right' : 'left']: 0 } as React.CSSProperties}>
      <div className="mx-auto h-full w-0.5 bg-transparent transition-colors group-hover/handle:bg-primary/60 group-focus-visible/handle:bg-primary" />
    </div>
  )
  return (
    <div className={`relative flex shrink-0 flex-col ${borderSide} border-outline-variant/40 bg-surface-low/40`} style={{ width }}>
      <div className="flex shrink-0 items-center justify-between gap-1 border-b border-outline-variant/40 px-2 py-1.5">
        <span data-type="label-s" className="inline-flex items-center gap-1.5 text-on-surface-var" style={fvs(550)}>
          <Icon size={14} /> {label}
        </span>
        <IconButton icon={side === 'left' ? PanelLeftClose : PanelRightClose} label={`Collapse ${label}`}
          onClick={() => setCollapsed(true)} size={24} iconSize={14} />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      {handle}
    </div>
  )
}


function FilesRail({ ws, isProjectDir, running }: { ws: string; isProjectDir: boolean; running: boolean }) {
  const [tab, setTab] = useState<'files' | 'changes'>('files')
  const gitWs = (isProjectDir || tab !== 'files') ? null : (ws || null)
  const [badgeNonce, setBadgeNonce] = useState(0)
  const { statuses: badgeStatuses } = useGitStatus(gitWs, badgeNonce)
  const changeCount = Object.keys(badgeStatuses).length
  useVisiblePoll(() => { if (gitWs) setBadgeNonce((n) => n + 1) }, running && gitWs ? 8000 : null)
  useEffect(() => {
    const onSaved = () => { if (gitWs) setBadgeNonce((n) => n + 1) }
    window.addEventListener('ne:code-file-saved', onSaved)
    return () => window.removeEventListener('ne:code-file-saved', onSaved)
  }, [gitWs])
  const tabRef = useRef(tab); tabRef.current = tab
  useEffect(() => {
    const onFocusFind = () => {
      if (tabRef.current !== 'files') {
        setTab('files')
        setTimeout(() => window.dispatchEvent(new CustomEvent('ne:code-focus-find')), 0)
      }
    }
    window.addEventListener('ne:code-focus-find', onFocusFind)
    return () => window.removeEventListener('ne:code-focus-find', onFocusFind)
  }, [])
  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-0.5 border-b border-outline-variant/40 px-2 py-1.5">
        <RailTab icon={FolderTree} label="Files" on={tab === 'files'} onClick={() => setTab('files')} />
        <RailTab icon={GitBranch} label="Changes" on={tab === 'changes'} onClick={() => setTab('changes')} badge={tab === 'files' ? changeCount : 0} />
      </div>
      {
}
      {tab === 'files' && !!ws && <FileFinder ws={ws} />}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === 'files' ? <WorkspaceTree ws={ws} running={running} isProjectDir={isProjectDir} />
          : <ChangesPanel ws={ws} running={running} isProjectDir={isProjectDir} />}
      </div>
    </div>
  )
}

function FileFinder({ ws }: { ws: string }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<{ path: string; name: string }[]>([])
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const qoId = useId()
  const [searching, setSearching] = useState(false)
  const seq = useRef(0)
  useEffect(() => {
    const needle = q.trim()
    if (needle.length < 2) { setResults([]); setSearching(false); return }
    const mine = ++seq.current
    setSearching(true)
    const t = setTimeout(() => {
      api.fileSearch(needle, ws).then((r) => {
        if (mine === seq.current) { setResults((r.results || []).map((x) => ({ path: x.path, name: x.name }))); setHi(0); setOpen(true); setSearching(false) }
      }).catch(() => { if (mine === seq.current) { setResults([]); setSearching(false) } })
    }, 200)
    return () => clearTimeout(t)
  }, [q, ws])
  const openFile = (r: { path: string; name: string }) => {
    window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: { name: r.name, path: r.path, is_dir: false } }))
    setQ(''); setResults([]); setOpen(false)
  }
  const rootRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => { if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [open])
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const onFocusFind = () => { inputRef.current?.focus(); inputRef.current?.select() }
    window.addEventListener('ne:code-focus-find', onFocusFind)
    return () => window.removeEventListener('ne:code-focus-find', onFocusFind)
  }, [])
  return (
    <div ref={rootRef} className="relative shrink-0 border-b border-outline-variant/40 px-2 py-1.5">
      <div className="flex items-center gap-1.5 rounded-md bg-surface-high px-2 py-1">
        <SearchField variant="inline" inlineIconSize={13} size="md" inputRef={inputRef} value={q}
          onChange={(v) => { setQ(v); if (!v) { setResults([]); setOpen(false) } }}
          onFocus={() => results.length && setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown' && results.length) { e.preventDefault(); setOpen(true); setHi((i) => Math.min(results.length - 1, i + 1)) }
            else if (e.key === 'ArrowUp' && results.length) { e.preventDefault(); setHi((i) => Math.max(0, i - 1)) }
            else if (e.key === 'Enter' && results.length) { e.preventDefault(); openFile(results[Math.min(hi, results.length - 1)]) }
            else if (e.key === 'Escape') { e.preventDefault(); setQ(''); setResults([]); setOpen(false) }
          }}
          placeholder="Find file by name…  ⌘P" spellCheck={false} autoCapitalize="off" autoCorrect="off"
          ariaLabel="Find file by name"
          ariaHasPopup="listbox" ariaControls={`${qoId}-list`}
          ariaExpanded={open && q.trim().length >= 2}
          ariaActiveDescendant={open && results.length ? `${qoId}-opt-${Math.min(hi, results.length - 1)}` : undefined} />
      </div>
      {open && q.trim().length >= 2 && (
        <div role="listbox" aria-label="Matching files" id={`${qoId}-list`}
          className="absolute inset-x-2 z-20 mt-1 max-h-[40vh] overflow-y-auto rounded-md border border-outline-variant/50 bg-surface-container shadow-lg">
          {results.length === 0 ? (
            searching
              ? <p data-type="caption" className="inline-flex items-center gap-1.5 px-2.5 py-2 text-on-surface-low"><Loader2 size={11} className="animate-spin" /> Searching…</p>
              : <p data-type="caption" className="px-2.5 py-2 text-on-surface-low">No files match “{q.trim()}”.</p>
          ) : results.map((r, i) => {
            const wsBase = ws.replace(/\/$/, '').split('/').pop() || ''
            const marker = `/${wsBase}/`
            const mi = wsBase ? r.path.lastIndexOf(marker) : -1
            const rel = mi >= 0 ? r.path.slice(mi + marker.length) : (r.path.startsWith(ws) ? r.path.slice(ws.replace(/\/$/, '').length + 1) : r.name)
            return (
              <button key={r.path} type="button" role="option" aria-selected={i === hi}
                id={`${qoId}-opt-${i}`}
                onClick={() => openFile(r)} onMouseEnter={() => setHi(i)}
                ref={(el) => { if (i === hi && open) el?.scrollIntoView({ block: 'nearest' }) }}
                data-type="caption" className={`flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left ${i === hi ? 'bg-surface-high' : 'hover:bg-surface-high'}`}>
                <FileCode size={11} className="shrink-0 text-on-surface-low" />
                <span className="min-w-0 truncate text-on-surface-var" title={rel}>{rel}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}


function RightPanel({ project, onTasksChanged, tasksNonce, activityBySession, gateFail, stalled, onNudged, onStartNew }: {
  project: CodeProject; onTasksChanged: () => void; tasksNonce: number
  activityBySession: Record<string, ActivityItem[]>; gateFail: { label: string; command: string; output: string } | null
  stalled: { stage: string; title: string; findings: number } | null; onNudged: () => void; onStartNew?: () => void
}) {
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const selectedRef = useRef<string | null>(selectedTaskId); selectedRef.current = selectedTaskId
  useEffect(() => {
    const onFocusSteer = () => {
      if (selectedRef.current !== null) {
        setSelectedTaskId(null)
        setTimeout(() => window.dispatchEvent(new CustomEvent('ne:code-focus-steer')), 0)
      }
    }
    window.addEventListener('ne:code-focus-steer', onFocusSteer)
    return () => window.removeEventListener('ne:code-focus-steer', onFocusSteer)
  }, [])
  const links = project.task_list_ids ?? {}
  const [tasksByList, setTasksByList] = useState<Record<string, TaskItem[]>>({})
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const refreshSeq = useRef(0)
  const refresh = useCallback(() => {
    const lists = Object.values(links)
    if (!lists.length) { setLoading(false); return }
    const seq = ++refreshSeq.current
    let anyFailed = false
    Promise.all(lists.map((lid) => api.allTasks({ task_list: lid }).then((r) => [lid, r.tasks] as const)
      .catch(() => { anyFailed = true; return [lid, null] as const })))
      .then((pairs) => {
        if (seq !== refreshSeq.current) return
        setTasksByList((prev) => {
          const next = { ...prev }
          for (const [lid, tasks] of pairs) if (tasks !== null) next[lid] = tasks
          return next
        })
        setLoadFailed(anyFailed)
      }).finally(() => { if (seq === refreshSeq.current) setLoading(false) })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(links)])
  useEffect(() => { refresh() }, [refresh, tasksNonce])

  const allTasks: TaskItem[] = Object.values(tasksByList).flat()
  const selected = selectedTaskId ? allTasks.find((t) => t.id === selectedTaskId) : null
  const selectionStale = !!selectedTaskId && !loading && allTasks.length > 0 && !selected
  useEffect(() => { if (selectionStale) setSelectedTaskId(null) }, [selectionStale])
  const findingsByTask: Record<string, CodeFinding[]> = {}
  const stages = project.stage_plan ?? []
  const tasksByStage: Record<string, TaskItem[]> = {}
  for (const s of stages) tasksByStage[stageKey(s)] = (links[stageKey(s)] && tasksByList[links[stageKey(s)]]) || []
  const stageTasksFor = (fstage: string): TaskItem[] => {
    const s = stages.find((sg) => sg.stage === fstage || sg.title === fstage)
      ?? stages.find((sg) => {
        const label = normalizeStageLabel(fstage)
        return label === normalizeStageLabel(sg.stage) || label === normalizeStageLabel(sg.title)
      })
    return s ? (tasksByStage[stageKey(s)] || []) : []
  }
  for (const f of (project.findings ?? [])) {
    let tid = f.task_id
    if (!tid && f.stage) {
      const st = stageTasksFor(f.stage)
      tid = (st.find((t) => t.status === 'in_progress')
        || st.find((t) => !isTerminalTask(t))
        || st[st.length - 1])?.id
    }
    if (tid) (findingsByTask[tid] ??= []).push(f)
  }

  if (selected) {
    const doneIds = new Set(allTasks.filter(isTerminalTask).map((t) => t.id))
    const taskSess = `loop-${project.id}-${selected.id}`
    const live = isTerminalTask(selected)
      ? []
      : activityBySession[taskSess]
        ?? (selected.status === 'in_progress' ? activityBySession[`loop-${project.id}`] : undefined)
        ?? []
    const ownerStage = stages.find((s) => (tasksByStage[stageKey(s)] || []).some((t) => t.id === selected.id))
    const ss = ownerStage ? (project.stage_status?.[stageKey(ownerStage)] ?? 'pending') : 'active'
    const stageOpen = ss === 'active' || ss === 'done'
    const byId = new Map(allTasks.map((t) => [t.id, t]))
    const blockers = (selected.dependencies ?? [])
      .map((d) => d.depends_on_task_id)
      .filter((bid): bid is string => !!bid && byId.has(bid) && !doneIds.has(bid))
      .map((bid) => ({ id: bid, title: byId.get(bid)!.title }))
    return <TaskDetailView key={selected.id} project={project} task={selected} doneIds={doneIds} stageOpen={stageOpen}
      knownIds={new Set(byId.keys())}
      findings={findingsByTask[selected.id] ?? []} liveActivity={live} blockers={blockers} onOpenTask={setSelectedTaskId}
      onBack={() => setSelectedTaskId(null)} onChanged={onTasksChanged} />
  }
  const activeTaskIds = new Set<string>()
  const pfx = `loop-${project.id}-`
  const doneForPulse = new Set(
    allTasks.filter(isTerminalTask).map((t) => t.id),
  )
  for (const [sess, items] of Object.entries(activityBySession)) {
    if (!sess.startsWith(pfx) || !items.length) continue
    const tid = sess.slice(pfx.length)
    if (!doneForPulse.has(tid)) activeTaskIds.add(tid)
  }
  return (
    <div className="flex h-full flex-col">
      {
}
      {loadFailed && (
        <div role="alert" data-type="caption" className="mx-2 mt-2 flex items-center gap-2 rounded-md px-2.5 py-1.5"
          style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)', color: 'var(--color-warn)' }}>
          <AlertTriangle size={12} className="shrink-0" />
          <span className="min-w-0 flex-1">Couldn't refresh some tasks — showing the last known list.</span>
          { }
          <Button variant="ghost" size="xs" onClick={refresh} className="shrink-0 gap-1 px-1.5 text-[0.75rem] text-warn hover:bg-warn/15"><RotateCcw size={11} /> Retry</Button>
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <StageTasks project={project} onTasksChanged={onTasksChanged} loading={loading}
          tasksByList={tasksByList} onSelect={setSelectedTaskId} activeTaskIds={activeTaskIds}
          mainActivity={activityBySession[`loop-${project.id}`] ?? []} />
      </div>
      { }
      <ProjectFooter project={project} gateFail={gateFail} stalled={stalled} onNudged={onNudged} onStartNew={onStartNew} />
    </div>
  )
}

function RailTab({ icon: Icon, label, on, onClick, badge }: { icon: typeof ListChecks; label: string; on: boolean; onClick: () => void; badge?: number }) {
  return (
    <button type="button" onClick={onClick}
      data-type="body-s" className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 transition-colors"
      style={on ? { background: 'var(--color-surface-high)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
      <Icon size={14} /> {label}
      {
}
      {!!badge && badge > 0 && (
        <span data-type="caption" className="ml-0.5 inline-flex min-w-[1.1rem] items-center justify-center rounded-full px-1 tabular-nums"
          style={{ background: on ? 'var(--color-primary)' : 'color-mix(in srgb, var(--color-primary) 22%, transparent)', color: on ? 'var(--color-on-primary)' : 'var(--color-primary)' }}>
          {badge > 99 ? '99+' : badge}
        </span>
      )}
    </button>
  )
}

const isTerminalTask = (t: TaskItem): boolean =>
  t.status === 'done' || t.status === 'completed' || t.status === 'cancelled'

type ExecState = 'done' | 'cancelled' | 'running' | 'queued' | 'blocked' | 'ready' | 'waiting'
function execState(t: TaskItem, doneIds: Set<string>, queued: Set<string>, stageActive = true, knownIds?: Set<string>): ExecState {
  if (t.status === 'done' || t.status === 'completed') return 'done'
  if (t.status === 'cancelled') return 'cancelled'
  if (t.status === 'in_progress') return 'running'
  const deps = (t.dependencies?.map((d) => d.depends_on_task_id) ?? t.depends_on ?? [])
  const blocked = deps.some((id) => !!id && !doneIds.has(id) && (!knownIds || knownIds.has(id)))
  if (blocked) return 'blocked'
  if (!stageActive) return 'waiting'
  return queued.has(t.id) ? 'queued' : 'ready'
}

function StageTasks({ project, onTasksChanged, loading, tasksByList, onSelect, activeTaskIds, mainActivity }: {
  project: CodeProject; onTasksChanged: () => void; loading: boolean
  tasksByList: Record<string, TaskItem[]>; onSelect: (taskId: string) => void; activeTaskIds: Set<string>
  mainActivity: ActivityItem[]
}) {
  const links = project.task_list_ids ?? {}
  const stages = project.stage_plan ?? []
  const [busy, setBusy] = useState(false)
  const noLists = !Object.keys(links).length
  const isPreLaunch = project.status === 'ready' || project.status === 'review'
  const queuedSet = new Set(project.queued_task_ids ?? [])
  const allTasks: TaskItem[] = Object.values(tasksByList).flat()
  const doneIds = new Set(allTasks.filter(isTerminalTask).map((t) => t.id))
  const knownIds = new Set(allTasks.map((t) => t.id))
  const queueable = allTasks.filter((t) => !doneIds.has(t.id) && !queuedSet.has(t.id))
  const runningCount = allTasks.filter((t) => t.status === 'in_progress').length

  async function queue(ids: string[]) {
    if (!ids.length || busy) return
    setBusy(true)
    try { await api.uLoopQueue(project.id, ids, 'queue'); onTasksChanged() }
    catch (e) { window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't queue ${ids.length > 1 ? 'those tasks' : 'that task'}: ${(e as Error).message || 'unknown error'}` } })) }
    finally { setBusy(false) }
  }
  const autopilot = project.autopilot !== false
  async function toggleAutopilot() {
    if (busy) return
    setBusy(true)
    try { await api.uLoopAutopilot(project.id, !autopilot); onTasksChanged() }
    catch (e) { window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't switch drive mode: ${(e as Error).message || 'unknown error'}` } })) }
    finally { setBusy(false) }
  }

  if (loading) return <Centered><Loader2 size={18} className="animate-spin text-on-surface-low" /></Centered>
  if (noLists && !stages.length) {
    if (!isPreLaunch && !TERMINAL_STATUSES.has(project.status) && mainActivity.length > 0) {
      return (
        <div className="flex flex-col gap-2 p-3">
          <p data-type="caption" className="text-on-surface-low">No task breakdown for this run — the worker is operating directly from the brief.</p>
          <div className="rounded-lg border border-primary/30 bg-primary/5 p-2.5">
            <div data-type="caption" className="mb-1.5 inline-flex items-center gap-1.5 text-primary"><Loader2 size={11} className="animate-spin" /> working now</div>
            <div className="flex flex-col gap-1">
              {mainActivity.map((it, i) => (
                it.kind === 'say'
                  ? <p key={i} data-type="caption" className="whitespace-pre-wrap text-on-surface-var">{it.label}</p>
                  : <div key={i} data-type="caption" className="flex items-start gap-1.5 text-on-surface-low">
                      {it.kind === 'tool' ? <Wrench size={11} className="mt-0.5 shrink-0" /> : <Activity size={11} className="mt-0.5 shrink-0" />}
                      <span className="min-w-0"><span className="text-on-surface-var">{it.label}</span>{it.detail && <span className="text-on-surface-low/70"> · {it.detail.slice(0, 60)}</span>}</span>
                    </div>
              ))}
            </div>
          </div>
        </div>
      )
    }
    const msg = isPreLaunch
      ? 'Tasks appear here once the project is launched.'
      : TERMINAL_STATUSES.has(project.status)
        ? 'This project ran without a task breakdown — see the activity and workspace files for what it did.'
        : 'No task breakdown for this run — the worker is operating directly from the brief. Watch its activity below as it works.'
    return <p data-type="body-s" className="px-3 py-6 text-center text-on-surface-low">{msg}</p>
  }
  return (
    <div className="flex flex-col gap-1 p-2">
      {!noLists && (
        <div className="flex items-center justify-between gap-2 px-2 pb-1">
          <span data-type="caption" className="inline-flex items-center gap-1.5 text-on-surface-low">
            {runningCount > 0 && (
              <span className="inline-flex items-center gap-1 text-primary" title="Tasks running in parallel worktrees">
                <Loader2 size={10} className="animate-spin" />{runningCount} running
              </span>
            )}
            {runningCount > 0 && (queuedSet.size > 0 || queueable.length > 0) && <span className="opacity-40">·</span>}
            {queuedSet.size > 0 && <span>{queuedSet.size} queued</span>}
            {queueable.length > 0 && <span className="opacity-70">{queueable.length} can queue</span>}
          </span>
          <div className="flex items-center gap-1.5">
            {
}
            <Button variant={autopilot ? 'tonal' : 'ghost'} size="xs" disabled={busy} disabledReason={BUSY_REASON}
              onClick={toggleAutopilot} ariaPressed={autopilot} ariaLabel="Autopilot"
              title={autopilot ? 'Autopilot on — the system queues + drives the phased tasks. Click for one-by-one.' : 'One-by-one — you queue tasks yourself. Click to let the system drive.'}>
              {autopilot ? <Rocket size={11} /> : <Hand size={11} />} {autopilot ? 'Autopilot' : 'One-by-one'}
            </Button>
            { }
            { }
            {!autopilot && queueable.length > 0 && (
              <Button variant="tonal" size="xs" disabled={busy} disabledReason={BUSY_REASON} onClick={() => queue(queueable.map((t) => t.id))}
                className="gap-1 px-2 text-[0.75rem]">
                <Play size={11} /> Queue all
              </Button>
            )}
          </div>
        </div>
      )}
      {noLists && isPreLaunch && (
        <p data-type="caption" className="px-2 pb-1 text-on-surface-low">Planned — provisioned when you launch.</p>
      )}
      {stages.map((s, si) => {
        const key = stageKey(s)
        const lid = links[key]
        const tasks: TaskItem[] = (lid && tasksByList[lid])
          || (noLists ? (s.tasks ?? []).map((t, i) => ({ id: `plan-${key}-${i}`, title: t.title, status: 'open' } as TaskItem)) : [])
        const st = project.stage_status?.[key] ?? 'pending'
        return (
          <StageGroup key={`${key}:${si}`} stage={s} status={st} tasks={tasks}
            preview={noLists} doneIds={doneIds} queuedSet={queuedSet} knownIds={knownIds}
            onSelect={onSelect} activeTaskIds={activeTaskIds}
            attention={['blocked', 'needs_input', 'stagnant', 'failed', 'stopped'].includes(project.status)} />
        )
      })}
    </div>
  )
}

function StageGroup({ stage: s, status: st, tasks, preview, doneIds, queuedSet, knownIds, onSelect, activeTaskIds, attention = false }: {
  stage: CodeStage; status: string; tasks: TaskItem[]; preview: boolean
  doneIds: Set<string>; queuedSet: Set<string>; knownIds: Set<string>; onSelect: (taskId: string) => void; activeTaskIds: Set<string>; attention?: boolean
}) {
  const color = st === 'active' ? (attention ? 'var(--color-warn)' : 'var(--color-primary)')
    : st === 'done' ? 'var(--color-ok)' : 'var(--color-on-surface-low)'
  const stageOpen = st === 'active' || st === 'done'
  const objective = (s.objective || '').trim()
  const criteria = (s.exit_criteria ?? []).filter((c) => (c || '').trim())
  const hasDetail = !!objective || criteria.length > 0
  const [open, setOpen] = useState(st === 'active')
  const prevSt = useRef(st)
  const rootRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (st === 'active' && prevSt.current !== 'active') {
      setOpen(true)
      requestAnimationFrame(() => rootRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
    }
    prevSt.current = st
  }, [st])
  const stLabel = st === 'active' ? 'active' : st === 'done' ? 'done' : 'pending'
  const a11yLabel = `${s.title || s.stage} — ${stLabel}, ${tasks.length} task${tasks.length === 1 ? '' : 's'}`
  return (
    <div ref={rootRef} className="rounded-lg">
      {hasDetail ? (
        <Expandable open={open} header={
          <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-label={a11yLabel}
            data-type="caption" className="flex w-full items-center gap-1.5 px-2 py-1.5 hover:bg-surface-high/40" style={{ color }}>
            <motion.span animate={{ rotate: open ? 90 : 0 }} transition={physics.snappy} className="shrink-0">
              <ChevronRight size={12} />
            </motion.span>
            <span className="truncate">{s.title || s.stage}</span>
            <span className="opacity-60">({tasks.length})</span>
          </button>
        }>
          <div className="mb-1 ml-4 flex flex-col gap-1.5 border-l border-outline-variant/40 pl-2.5">
            {objective && <p data-type="caption" className="text-on-surface-var normal-case">{objective}</p>}
            {criteria.length > 0 && (
              <div className="flex flex-col gap-0.5">
                <Eyebrow as="span">Done when</Eyebrow>
                {criteria.map((c, i) => (
                  <div key={i} data-type="caption" className="flex items-start gap-1.5 text-on-surface-low normal-case">
                    <Target size={9} className="mt-[3px] shrink-0 opacity-60" /><span>{c}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Expandable>
      ) : (
        <div role="group" aria-label={a11yLabel}
          data-type="caption" className="flex w-full items-center gap-1.5 px-2 py-1.5" style={{ color }}>
          <span className="truncate">{s.title || s.stage}</span>
          <span className="opacity-60">({tasks.length})</span>
        </div>
      )}
      {tasks.length === 0
        ? <p data-type="caption" className="px-3 pb-2 text-on-surface-low/70">no tasks yet</p>
        : <motion.div variants={{ animate: { transition: stagger() } }} initial="initial" animate="animate">
            {tasks.map((t) => (
              <motion.div key={t.id} variants={listItemEnter}>
                <TaskRow task={t} preview={preview}
                  state={execState(t, doneIds, queuedSet, stageOpen, knownIds)} active={activeTaskIds.has(t.id)} onSelect={() => onSelect(t.id)} />
              </motion.div>
            ))}
          </motion.div>}
    </div>
  )
}

const STATE_ICON: Record<ExecState, typeof Circle> = {
  done: CheckCircle2, cancelled: XCircle, running: Loader2, queued: Clock, blocked: Circle, ready: CirclePlay, waiting: Clock,
}
const STATE_COLOR: Record<ExecState, string> = {
  done: 'var(--color-ok)', cancelled: 'var(--color-on-surface-low)', running: 'var(--color-primary)',
  queued: 'var(--color-primary)', blocked: 'var(--color-on-surface-low)', ready: 'var(--color-on-surface-var)',
  waiting: 'var(--color-on-surface-low)',
}
const STATE_LABEL: Record<ExecState, string> = {
  done: 'done', cancelled: 'cancelled', running: 'running', queued: 'queued', blocked: 'blocked', ready: 'ready to queue', waiting: 'waiting for its stage',
}

function TaskRow({ task, state, preview, active, onSelect }: {
  task: TaskItem; state: ExecState; preview: boolean; active?: boolean; onSelect: () => void
}) {
  const Icon = STATE_ICON[state]
  const done = state === 'done' || state === 'cancelled'
  if (preview) {
    return (
      <div data-type="body-s" className="flex items-start gap-1.5 px-2 py-1.5">
        <Icon size={14} className="mt-0.5 shrink-0" style={{ color: STATE_COLOR[state] }} />
        <span className="min-w-0 flex-1 text-on-surface-var">{task.title}</span>
      </div>
    )
  }
  return (
    <button type="button" onClick={onSelect} aria-label={`${task.title} — ${STATE_LABEL[state]}`}
      data-type="body-s" className="group flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left hover:bg-surface-high/60">
      <Icon size={14} className={`shrink-0 ${state === 'running' ? 'animate-spin' : ''}`} style={{ color: STATE_COLOR[state] }} />
      <span className={`min-w-0 flex-1 truncate ${done ? 'text-on-surface-low line-through' : 'text-on-surface-var'}`}>{task.title}</span>
      { }
      {active && <span className="reveal-caret size-1.5 shrink-0 rounded-full" style={{ background: 'var(--color-primary)' }} title="Active now" />}
      {state === 'cancelled' && <span data-type="caption" className="shrink-0 text-on-surface-low/70">cancelled</span>}
      {state === 'running' && <span data-type="caption" className="shrink-0 text-primary">running</span>}
      {state === 'queued' && <span data-type="caption" className="shrink-0 text-primary">queued</span>}
      {state === 'blocked' && <span data-type="caption" className="shrink-0 text-on-surface-low/70">blocked</span>}
      {state === 'waiting' && <span data-type="caption" className="shrink-0 text-on-surface-low/70" title="Waiting for its stage to start">waiting</span>}
      {
}
      {state === 'ready' && <span data-type="caption" className="shrink-0 text-on-surface-low/70" title="Ready to queue">ready</span>}
      <ChevronRight size={13} className="shrink-0 text-on-surface-low opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100" />
    </button>
  )
}

function BlockerList({ blockers, onOpenTask }: { blockers: { id: string; title: string }[]; onOpenTask: (id: string) => void }) {
  if (!blockers.length) return null
  return (
    <div className="mt-1 flex flex-col gap-0.5">
      {blockers.map((b) => (
        <button key={b.id} type="button" onClick={() => onOpenTask(b.id)} title={`Open “${b.title}”`}
          className="group/bl inline-flex max-w-full items-center gap-1 self-start rounded px-1 text-on-surface-var hover:text-primary">
          <Circle size={9} className="shrink-0 opacity-60" />
          <span className="truncate">{b.title}</span>
          <ChevronRight size={11} className="shrink-0 opacity-0 transition-opacity group-hover/bl:opacity-100 focus-within:opacity-100" />
        </button>
      ))}
    </div>
  )
}

function TaskDetailView({ project, task, doneIds, stageOpen, knownIds, findings, liveActivity, blockers, onOpenTask, onBack, onChanged }: {
  project: CodeProject; task: TaskItem; doneIds: Set<string>; stageOpen: boolean; knownIds: Set<string>; findings: CodeFinding[]; liveActivity: ActivityItem[]
  blockers: { id: string; title: string }[]; onOpenTask: (taskId: string) => void
  onBack: () => void; onChanged: () => void
}) {
  const state = execState(task, doneIds, new Set(project.queued_task_ids ?? []), stageOpen, knownIds)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [showAllFindings, setShowAllFindings] = useState(false)
  const actingRef = useRef(false)
  const steerRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { autoGrowTextarea(steerRef.current) }, [text])
  const [lastSteer, setLastSteer] = useState<{ text: string; failed?: boolean } | null>(null)
  const queued = (project.queued_task_ids ?? []).includes(task.id)
  const autopilot = project.autopilot !== false
  const done = task.status === 'done' || task.status === 'completed'
  const terminal = isTerminalTask(task)
  const running = task.status === 'in_progress'
  const live = running ? liveActivity : []
  const plan = task.action_plan ?? []
  const crit = task.exit_criteria ?? []
  const ws = project.workspace_dir || project.files_dir || ''

  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [findings.length, live.length])

  async function queue(action: 'queue' | 'unqueue') {
    if (busy || actingRef.current) return
    actingRef.current = true
    setBusy(true)
    try { await api.uLoopQueue(project.id, [task.id], action); onChanged() }
    catch (e) { window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't ${action === 'queue' ? 'queue' : 'unqueue'} this task: ${(e as Error).message || 'unknown error'}` } })) }
    finally { setBusy(false); actingRef.current = false }
  }
  async function steer(explicit?: string) {
    const t = (explicit ?? text).trim()
    if (!t || busy || actingRef.current) return
    actingRef.current = true
    setBusy(true)
    try {
      await api.uLoopNudge(project.id, `[For task "${task.title}"] ${t}`, task.id)
      if (explicit === undefined) setText('')
      setLastSteer({ text: t }); onChanged()
    } catch (e) {
      setLastSteer({ text: t, failed: true })
      window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't send that steer: ${(e as Error).message || 'unknown error'}` } }))
    } finally { setBusy(false); actingRef.current = false }
  }

  return (
    <div className="flex h-full flex-col">
      { }
      <div className="flex shrink-0 items-center gap-1.5 border-b border-outline-variant/40 px-2 py-1.5">
        { }
        <Button variant="ghost" size="xs" onClick={onBack} className="gap-1 px-1.5 text-[0.75rem] text-on-surface-low">
          <ChevronLeft size={14} /> Tasks
        </Button>
        <span data-type="caption" className="ml-auto inline-flex items-center gap-1" style={{ color: running ? 'var(--color-primary)' : done ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }}>
          {running && <Loader2 size={10} className="animate-spin" />}{running ? 'running' : queued ? 'queued' : done ? 'done' : task.status}
        </span>
      </div>
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto p-3">
        <h3 data-type="title-m" className="text-on-surface" style={fvs(600)}>{task.title}</h3>
        {task.description && <p data-type="body-s" className="mt-1 text-on-surface-var">{task.description}</p>}

        { }
        {project.status === 'needs_input' && project.pending_question?.question && (
          <div data-type="body-s" className="mt-3 rounded-lg p-2.5" style={{ background: 'color-mix(in srgb, var(--color-info) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-info)' }, 550)}>
              <HelpCircle size={14} /> Needs your input
            </div>
            <p className="whitespace-pre-wrap text-on-surface">{project.pending_question.question}</p>
            {project.pending_question.why && (
              <p data-type="caption" className="mt-1 whitespace-pre-wrap text-on-surface-low">{project.pending_question.why}</p>
            )}
            <div className="mt-2 flex items-center gap-2">
              <p data-type="caption" className="flex-1 text-on-surface-low">Answer in the box below to resume.</p>
              {
}
              { }
              <Button variant="ghost" size="xs" disabled={busy} disabledReason={BUSY_REASON}
                onClick={() => steer('Proceed with your best judgment / the sensible default you proposed. Record the assumption in your finding and continue.')}
                className="shrink-0 px-2 text-[0.75rem] text-info hover:bg-info/10">
                Use your best judgment
              </Button>
            </div>
          </div>
        )}

        {plan.length > 0 && (
          <div className="mt-3">
            <Eyebrow as="p">Action plan</Eyebrow>
            <ol className="mt-1 flex flex-col gap-0.5">
              {plan.map((a, i) => (
                <li key={i} data-type="caption" className={`flex items-start gap-1.5 ${a.completed ? 'text-on-surface-low line-through' : 'text-on-surface-var'}`}>
                  <span className="mt-[1px] shrink-0 tabular-nums text-on-surface-low">{i + 1}.</span><span>{a.content}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
        {crit.length > 0 && (
          <div className="mt-3">
            <Eyebrow as="p">Done when</Eyebrow>
            <ul className="mt-1 flex flex-col gap-0.5">
              {crit.map((c, i) => (
                <li key={i} data-type="caption" className="flex items-start gap-1.5 text-on-surface-low">
                  <Target size={9} className="mt-[4px] shrink-0 opacity-60" /><span className={c.met ? 'line-through opacity-70' : ''}>{c.description}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        { }
        <div className="mt-3">
          <Eyebrow as="p">Agent activity{findings.length ? ` · ${findings.length} ${findings.length === 1 ? 'cycle' : 'cycles'}` : ''}</Eyebrow>
          {findings.length === 0 && live.length === 0 && (
            <p data-type="caption" className="mt-1 text-on-surface-low/70">{running ? 'Working…' : queued ? 'Queued — will run when ready.' : 'No activity yet.'}</p>
          )}
          <div className="mt-1 flex flex-col gap-1.5">
            {
}
            {(() => {
              const CAP = 6
              const hidden = showAllFindings ? 0 : Math.max(0, findings.length - CAP)
              const shown = hidden > 0 ? findings.slice(-CAP) : findings
              return (
                <>
                  {hidden > 0 && (
                    <TextLink size="xs" ink="emphasis" className="self-start"
                      onClick={() => setShowAllFindings(true)}
                      title={`Show ${hidden} earlier cycle${hidden === 1 ? '' : 's'}`}>↑ {hidden} earlier cycle{hidden === 1 ? '' : 's'}</TextLink>
                  )}
                  {shown.map((f) => <FindingCard key={f.cycle ?? `${f.summary}`} finding={f} ws={ws} />)}
                </>
              )
            })()}
          </div>
          {live.length > 0 && (
            <div className="mt-2 rounded-lg border border-primary/30 bg-primary/5 p-2.5">
              <div data-type="caption" className="mb-1.5 inline-flex items-center gap-1.5 text-primary"><Loader2 size={11} className="animate-spin" /> working now</div>
              <div className="flex flex-col gap-1">
                {live.map((it, i) => (
                  it.kind === 'say'
                    ? <p key={i} data-type="caption" className="whitespace-pre-wrap text-on-surface-var">{it.label}</p>
                    : <div key={i} data-type="caption" className="flex items-start gap-1.5 text-on-surface-low">
                        {it.kind === 'tool' ? <Wrench size={11} className="mt-0.5 shrink-0" /> : <Activity size={11} className="mt-0.5 shrink-0" />}
                        <span className="min-w-0"><span className="text-on-surface-var">{it.label}</span>{it.detail && <span className="text-on-surface-low/70"> · {it.detail.slice(0, 60)}</span>}</span>
                      </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {
}
      <div className="shrink-0 border-t border-outline-variant/40 p-2">
        {autopilot ? (
          !terminal && (
            <>
              <p data-type="caption" className="mb-2 inline-flex items-center gap-1 text-on-surface-low">
                <Rocket size={11} className="text-primary" />
                {running ? 'Running on autopilot.'
                  : state === 'blocked' ? 'Autopilot will run it once its dependencies finish.'
                  : state === 'waiting' ? 'Autopilot will run it when its stage starts.'
                  : 'Queued by autopilot.'}
              </p>
              {state === 'blocked' && blockers.length > 0 && (
                <div data-type="caption" className="mb-2 text-on-surface-low">
                  <span>Waiting on:</span>
                  <BlockerList blockers={blockers} onOpenTask={onOpenTask} />
                </div>
              )}
            </>
          )
        ) : (
          <>
            {
}
            { }
            {(state === 'ready' || (state === 'waiting' && !queued)) && (
              <Button variant="tonal" size="xs" disabled={busy} disabledReason={BUSY_REASON} onClick={() => queue('queue')}
                className="mb-2 gap-1 px-2 text-[0.75rem]">
                <Play size={11} /> Queue this task{state === 'waiting' ? ' (runs when its stage starts)' : ''}
              </Button>
            )}
            {state === 'waiting' && queued && (
              <p data-type="caption" className="mb-2 text-on-surface-low">Queued · waiting for its stage to start.</p>
            )}
            {state === 'blocked' && (
              <div data-type="caption" className="mb-2 text-on-surface-low">
                <span>Blocked — waiting on{queued ? ' (queued; will run when they finish)' : ''}:</span>
                <BlockerList blockers={blockers} onOpenTask={onOpenTask} />
              </div>
            )}
            {queued && !running && (state === 'queued') && (
              <TextLink disabled={busy} onClick={() => queue('unqueue')} size="xs" className="mb-2">Remove from queue</TextLink>
            )}
            {queued && !running && (state === 'blocked' || state === 'waiting') && (
              <TextLink disabled={busy} onClick={() => queue('unqueue')} size="xs" className="mb-2 ml-2">Remove from queue</TextLink>
            )}
          </>
        )}
        {!STEERABLE.has(project.status) ? (
          <p data-type="caption" className="px-1.5 py-1 text-center text-on-surface-low">
            {TERMINAL_STATUSES.has(project.status) ? 'This project has finished.' : 'This project hasn’t started yet.'}
          </p>
        ) : terminal ? (
          <p data-type="caption" className="px-1.5 py-1 text-center text-on-surface-low">
            This task is {task.status === 'cancelled' ? 'cancelled' : 'done'} — steer the project or another task to direct further work.
          </p>
        ) : (
          <>
            {
}
            {lastSteer && (
              <div data-type="body-s" className="mb-2 self-end rounded-xl bg-primary/15 px-2.5 py-1.5 text-on-surface-var">
                {lastSteer.text}
                {lastSteer.failed && <span data-type="caption" className="ml-1.5 text-danger">· failed to send</span>}
              </div>
            )}
            <div className="flex items-end gap-1.5 rounded-xl bg-surface-container px-2.5 py-1.5 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
              <textarea ref={steerRef} value={text} onChange={(e) => setText(e.target.value)} rows={1}
                placeholder={project.status === 'needs_input' ? 'Answer for this task…' : `Steer “${task.title.slice(0, 24)}${task.title.length > 24 ? '…' : ''}”…`}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); steer() } }}
                data-type="body-s" className="max-h-24 min-h-0 flex-1 resize-none overflow-y-auto bg-transparent text-on-surface outline-none placeholder:text-on-surface-low" />
              {
}
              <IconButton icon={Send} label="Send steer" filled size={28} iconSize={13}
                disabled={!text.trim()} disabledReason={!text.trim() ? 'Type a steer first' : undefined}
                loading={busy} onClick={() => steer()} className="shrink-0" />
            </div>
          </>
        )}
      </div>
    </div>
  )
}

const PROJECT_DIR_HIDDEN = new Set(['brief.md', 'status.json', 'findings', 'verdicts', 'FINDINGS.md', 'guidance.txt', 'questions.json', 'nudges.json', 'STOP', 'plan_session.json', 'plan_steps.json', 'step_artifact.json', 'plan.json'])
const PROJECT_DIR_HIDDEN_PREFIXES = new Set(['guidance_'])

const CODE_TREE_NOISE = new Set(['.git', '.hg', '.svn', '__pycache__', '.pytest_cache',
  '.mypy_cache', '.ruff_cache', '.tox', 'node_modules', '.venv', 'venv', '.DS_Store',
  '.idea', '.next', '.turbo', '.cache', '.gradle', '__snapshots__', '.terraform'])

function WorkspaceTree({ ws, running, isProjectDir }: { ws: string; running: boolean; isProjectDir: boolean }) {
  const dirs = useDirCache()
  const [gitNonce, setGitNonce] = useState(0)
  const { statuses, state: gitState } = useGitStatus(ws || null, gitNonce)
  useVisiblePoll(() => {
    if (ws) { dirs.invalidateSubtree(ws); setGitNonce((n) => n + 1) }
  }, running && ws ? 8000 : null)
  useEffect(() => {
    const onSaved = () => { if (ws) { dirs.invalidate(ws); setGitNonce((n) => n + 1) } }
    window.addEventListener('ne:code-file-saved', onSaved)
    return () => window.removeEventListener('ne:code-file-saved', onSaved)
  }, [ws, dirs])
  const erasedRef = useRef<Set<string>>(new Set())
  const eraseSeededRef = useRef(false)
  useEffect(() => {
    if (!running || gitState !== 'loaded') return
    const seeding = !eraseSeededRef.current
    for (const [path, code] of Object.entries(statuses)) {
      if (!code.includes('D') || erasedRef.current.has(path)) continue
      erasedRef.current.add(path)
      if (seeding) continue
      api.fileGitOriginal(path).then((r) => {
        if (r?.content?.trim()) {
          window.dispatchEvent(new CustomEvent('ne:code-erase-file', {
            detail: { path, name: path.split('/').pop(), text: r.content },
          }))
        }
      }).catch(() => {})
    }
    eraseSeededRef.current = true
  }, [statuses, running, gitState])
  const openFile = useCallback((entry: FsEntry) => {
    window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: entry }))
  }, [])
  const [activePath, setActivePath] = useState<string | null>(null)
  useEffect(() => {
    const onOpen = (e: Event) => {
      const d = (e as CustomEvent).detail as { path?: string; is_dir?: boolean } | null
      if (d?.path && !d.is_dir) setActivePath(d.path)
    }
    window.addEventListener('ne:code-open-file', onOpen as EventListener)
    return () => window.removeEventListener('ne:code-open-file', onOpen as EventListener)
  }, [])
  const [creating, setCreating] = useState<'file' | 'dir' | null>(null)
  const [newName, setNewName] = useState('')
  const [createErr, setCreateErr] = useState('')
  const creatingInFlight = useRef(false)
  const [treeErr, setTreeErr] = useState('')
  const doDelete = async (entry: FsEntry) => {
    if (!(await confirm({
      title: entry.is_dir ? `Delete folder "${entry.name}"?` : `Delete file "${entry.name}"?`,
      body: entry.is_dir
        ? "This deletes the folder and all its contents. This can't be undone."
        : "This can't be undone.",
      danger: true, confirmLabel: 'Delete',
    }))) return
    setTreeErr('')
    let ok = false
    try {
      await api.fileDelete(entry.path)
      ok = true
      window.dispatchEvent(new CustomEvent('ne:code-close-file', { detail: { path: entry.path } }))
    }
    catch (e) { setTreeErr(`Couldn't delete "${entry.name}": ${(e as Error).message || 'unknown error'}`) }
    if (ok) {
      dirs.invalidate(entry.path.slice(0, entry.path.length - entry.name.length).replace(/\/$/, '') || ws)
      dirs.invalidate(ws)
      window.dispatchEvent(new CustomEvent('ne:code-file-saved'))
    }
  }
  const submitCreate = async () => {
    if (creatingInFlight.current) return
    const name = newName.trim()
    if (!name || !creating) { setCreating(null); setNewName(''); setCreateErr(''); return }
    creatingInFlight.current = true
    try {
      const r = await api.fileCreate(ws, name, creating)
      if (creating === 'file') window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: { name, path: r.path, is_dir: false } }))
      setCreating(null); setNewName(''); setCreateErr(''); dirs.invalidate(ws)
      window.dispatchEvent(new CustomEvent('ne:code-file-saved'))
    } catch (e) {
      const msg = (e as Error).message || 'Could not create'
      setCreateErr(/already exists/i.test(msg) ? 'A file or folder with that name already exists.'
        : /invalid name/i.test(msg) ? 'Invalid name — no slashes; pick a different name.'
        : msg)
      dirs.invalidate(ws)
    } finally {
      creatingInFlight.current = false
    }
  }
  const cancelCreate = () => { setCreating(null); setNewName(''); setCreateErr('') }
  if (!ws) return <p data-type="body-s" className="px-3 py-6 text-center text-on-surface-low">No files yet for this project.</p>
  return (
    <div className="p-1">
      {
}
      {!isProjectDir && (
        <div className="flex flex-col gap-1 px-1.5 pb-1">
          <div className="flex items-center gap-1">
          {creating ? (
            <input autoFocus value={newName} onChange={(e) => { setNewName(e.target.value); if (createErr) setCreateErr('') }}
              onKeyDown={(e) => { if (e.key === 'Enter') submitCreate(); if (e.key === 'Escape') cancelCreate() }}
              onBlur={() => { if (!createErr) submitCreate() }} placeholder={creating === 'file' ? 'new-file.ext' : 'new-folder'}
              data-type="body-s" className={`h-7 min-w-0 flex-1 rounded-md bg-surface-high px-2 text-on-surface outline-none focus:ring-2 placeholder:text-on-surface-low ${createErr ? 'focus:ring-danger/50 ring-2 ring-danger/40' : 'focus:ring-primary'}`} />
          ) : (
            <>
              { }
              <Button variant="ghost" size="xs" title="New file" onClick={() => { setCreating('file'); setNewName('') }}
                className="gap-1 px-1.5 text-[0.75rem] text-on-surface-low"><FilePlus2 size={13} /> File</Button>
              <Button variant="ghost" size="xs" title="New folder" onClick={() => { setCreating('dir'); setNewName('') }}
                className="gap-1 px-1.5 text-[0.75rem] text-on-surface-low"><FolderPlus size={13} /> Folder</Button>
            </>
          )}
          </div>
          {createErr && <span role="alert" data-type="caption" className="px-0.5 text-danger">{createErr}</span>}
        </div>
      )}
      {
}
      {treeErr && (
        <div role="alert" data-type="caption" className="mx-1.5 mb-1 flex items-start gap-1.5 px-0.5 text-danger">
          <span className="min-w-0 flex-1">{treeErr}</span>
          <IconButton icon={X} label="Dismiss" onClick={() => setTreeErr('')} size={18} iconSize={11} className="shrink-0" />
        </div>
      )}
      <FileTree dirs={dirs} rootPath={ws} activePath={activePath} gitStatuses={statuses}
        onOpenFile={openFile} artifactPaths={EMPTY_ARTIFACTS}
        hideNames={isProjectDir ? PROJECT_DIR_HIDDEN : undefined}
        hidePrefixes={isProjectDir ? PROJECT_DIR_HIDDEN_PREFIXES : undefined}
        hideNamesDeep={CODE_TREE_NOISE}
        emptyLabel={isProjectDir
          ? (running ? 'No files yet — the worker will create them here.' : 'No files yet — files the worker creates will appear here once it runs.')
          : 'Empty'}
        onRename={async (entry, nextName) => {
          const parent = entry.path.slice(0, entry.path.length - entry.name.length).replace(/\/$/, '')
          setTreeErr('')
          try {
            const dest = parent ? `${parent}/${nextName}` : nextName
            await api.fileMove(entry.path, dest)
            window.dispatchEvent(new CustomEvent('ne:code-renamed', { detail: { from: entry.path, to: dest, isDir: !!entry.is_dir } }))
          }
          catch (e) {
            const msg = (e as Error).message || 'unknown error'
            setTreeErr(/already exists/i.test(msg)
              ? `Couldn't rename "${entry.name}" — a file or folder named "${nextName}" already exists here.`
              : `Couldn't rename "${entry.name}": ${msg}`)
          }
          dirs.invalidate(parent || ws); dirs.invalidate(ws)
          window.dispatchEvent(new CustomEvent('ne:code-file-saved'))
        }}
        onDelete={(entry) => { void doDelete(entry) }}
        onUpload={async (dirEntry, files) => {
          if (!files.length) return
          setTreeErr('')
          try {
            const r = await api.fileUpload(dirEntry.path, files)
            if (!r.ok) setTreeErr(`Couldn't upload to ${dirEntry.name}: ${r.error || 'unknown error'}`)
          } catch (e) {
            setTreeErr(`Couldn't upload to ${dirEntry.name}: ${(e as Error).message || 'unknown error'}`)
          }
          dirs.invalidate(dirEntry.path); dirs.invalidate(ws)
          window.dispatchEvent(new CustomEvent('ne:code-file-saved'))
        }}
        onCreate={async (dirEntry, name, kind) => {
          setTreeErr('')
          try {
            const r = await api.fileCreate(dirEntry.path, name, kind)
            if (kind === 'file') window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: { name, path: r.path, is_dir: false } }))
          } catch (e) {
            const msg = (e as Error).message || 'unknown error'
            setTreeErr(/already exists/i.test(msg) ? `"${name}" already exists in ${dirEntry.name}.`
              : /invalid name/i.test(msg) ? `Invalid name "${name}" — no slashes.` : `Couldn't create "${name}": ${msg}`)
          }
          dirs.invalidate(dirEntry.path); dirs.invalidate(ws)
          window.dispatchEvent(new CustomEvent('ne:code-file-saved'))
        }} />
    </div>
  )
}

function ChangesPanel({ ws, running, isProjectDir = false }: { ws: string; running: boolean; isProjectDir?: boolean }) {
  const [nonce, setNonce] = useState(0)
  const gitWs = isProjectDir ? null : (ws || null)
  const { branch, statuses, state, repoRoot } = useGitStatus(gitWs, nonce)
  useVisiblePoll(() => { if (gitWs) setNonce((n) => n + 1) }, running && gitWs ? 8000 : null)
  useEffect(() => {
    const onSaved = () => { if (gitWs) setNonce((n) => n + 1) }
    window.addEventListener('ne:code-file-saved', onSaved)
    return () => window.removeEventListener('ne:code-file-saved', onSaved)
  }, [gitWs])
  const [commits, setCommits] = useState<{ hash: string; subject: string; relative: string }[]>([])
  useEffect(() => {
    if (!gitWs) { setCommits([]); return }
    let alive = true
    api.fileGitLog(gitWs, 20).then((r) => { if (alive) setCommits(r.commits || []) }).catch(() => { if (alive) setCommits([]) })
    return () => { alive = false }
  }, [gitWs, nonce])

  if (isProjectDir) return <p data-type="body-s" className="px-3 py-6 text-center text-on-surface-low">This project has no git workspace — changes aren't version-tracked. Files the worker created are in the Files tab.</p>
  if (!ws) return <p data-type="body-s" className="px-3 py-6 text-center text-on-surface-low">No workspace directory.</p>
  if (state === 'loaded' && !repoRoot) return <p data-type="body-s" className="px-3 py-6 text-center text-on-surface-low">This workspace isn’t a git repository — changes aren’t version-tracked. Browse the files in the Files tab.</p>
  const entries = Object.entries(statuses)
  const label = (code: string): { text: string; color: string } => {
    const c = code.trim()
    if (c === '??') return { text: 'new', color: 'var(--color-ok)' }
    if (c.includes('U') || c === 'AA' || c === 'DD') return { text: 'conflict', color: 'var(--color-danger)' }
    if (c.includes('D')) return { text: 'deleted', color: 'var(--color-danger)' }
    if (c.includes('A')) return { text: 'added', color: 'var(--color-ok)' }
    if (c.includes('R')) return { text: 'renamed', color: 'var(--color-primary)' }
    return { text: 'modified', color: 'var(--color-warn)' }
  }
  const wsBase = ws.replace(/\/$/, '').split('/').pop() || ''
  const rel = (p: string) => {
    const marker = `/${wsBase}/`
    const i = wsBase ? p.lastIndexOf(marker) : -1
    return i >= 0 ? p.slice(i + marker.length) : p
  }
  const open = (gitPath: string, code: string) => {
    const r = rel(gitPath)
    const path = `${ws.replace(/\/$/, '')}/${r}`
    const deleted = code.includes('D')
    window.dispatchEvent(new CustomEvent('ne:code-open-diff', { detail: { name: r.split('/').pop(), path, deleted } }))
  }
  return (
    <div className="flex flex-col">
      <div data-type="caption" className="flex items-center justify-between gap-2 px-3 py-2 text-on-surface-low">
        <span className="inline-flex items-center gap-1.5 min-w-0">
          <GitBranch size={12} className="shrink-0" />
          <span className="truncate text-on-surface-var">{branch || (state === 'loaded' ? '(no branch)' : '…')}</span>
        </span>
        { }
        <Button variant="ghost" size="xs" onClick={() => setNonce((n) => n + 1)} className="shrink-0 px-1.5 text-[0.75rem] text-on-surface-low">refresh</Button>
      </div>
      {entries.length === 0 ? (
        state === 'loading' || state === 'idle' ? (
          <p data-type="body-s" className="px-3 py-6 text-center text-on-surface-low">Loading changes…</p>
        ) : state === 'error' ? (
          <div data-type="body-s" className="flex flex-col items-center gap-2 px-3 py-6 text-center">
            <span style={{ color: 'var(--color-warn)' }}>Couldn't read git status for this workspace.</span>
            <Button variant="ghost" size="xs" onClick={() => setNonce((n) => n + 1)}>Retry</Button>
          </div>
        ) : (
          <p data-type="body-s" className="px-3 py-6 text-center text-on-surface-low">No uncommitted changes — the working tree is clean.</p>
        )
      ) : (
        <div className="flex flex-col">
          {
}
          <Eyebrow className="px-3 pt-1 pb-1">
            Changes ({entries.length})
          </Eyebrow>
          {entries.map(([path, code]) => {
            const l = label(code)
            return (
              <button key={path} type="button" onClick={() => open(path, code)}
                data-type="body-s" className="flex items-center gap-2 px-3 py-1.5 text-left transition-colors hover:bg-surface-high">
                <span data-type="caption" className="w-[58px] shrink-0" style={{ color: l.color }}>{l.text}</span>
                <span className="min-w-0 truncate font-mono text-on-surface-var" title={path}>{rel(path)}</span>
              </button>
            )
          })}
        </div>
      )}
      {
}
      {commits.length > 0 && (
        <div className="mt-1 border-t border-outline-variant/40">
          <div className="flex items-baseline justify-between gap-2 px-3 pt-2 pb-1">
            <Eyebrow as="span">History</Eyebrow>
            {
}
            {commits.length >= 20 && <span data-type="caption" className="text-on-surface-low/70">latest 20</span>}
          </div>
          {commits.map((c) => (
            <button key={c.hash} type="button"
              onClick={() => window.dispatchEvent(new CustomEvent('ne:code-open-commit', { detail: { hash: c.hash, subject: c.subject, path: ws } }))}
              data-type="body-s" className="flex w-full items-baseline gap-2 px-3 py-1 text-left transition-colors hover:bg-surface-high">
              <span data-type="caption" className="shrink-0 font-mono text-on-surface-low">{c.hash}</span>
              <span className="min-w-0 flex-1 truncate text-on-surface-var" title={c.subject}>{c.subject}</span>
              <span data-type="caption" className="shrink-0 text-on-surface-low">{c.relative}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}


function CenterEditor({ ws, showTerm, onCloseTerm, running, runCmd }: { ws: string; showTerm: boolean; onCloseTerm: () => void; running: boolean; runCmd?: { cmd: string; n: number } | null }) {
  const [termOpened, setTermOpened] = useState(false)
  useEffect(() => { if (showTerm) setTermOpened(true) }, [showTerm])
  const closeTerm = useCallback(() => { setTermOpened(false); onCloseTerm() }, [onCloseTerm])
  const { tabs, active, activePath, dirty, open, closeNow, setActivePath, markDirty } = useFileTabs(ws)
  const tabsRef = useRef(tabs); tabsRef.current = tabs
  const activePathRef = useRef(activePath); activePathRef.current = activePath
  const requestClose = useCallback(async (path: string, name: string) => {
    if (!dirty[path]) { closeNow(path); return }
    if (!(await confirm({
      title: 'Discard unsaved changes?',
      body: `"${name}" has edits that haven't been saved. Closing the tab discards them.`,
      danger: true, confirmLabel: 'Discard',
    }))) return
    draftStore.delete(path); closeNow(path)
  }, [dirty, closeNow])
  const viewerRef = useRef<FileViewerHandle>(null)
  const draftStore = useRef(new Map<string, { draft: string; base: string; warned: boolean }>()).current
  const [collapsedPanels, setCollapsedPanels] = useState<Record<string, { side: 'left' | 'right'; label: string }>>(() => {
    const seed: Record<string, { side: 'left' | 'right'; label: string }> = {}
    try {
      if (localStorage.getItem('code-left-collapsed') === '1') seed['code-left'] = { side: 'left', label: 'Files' }
      if (localStorage.getItem('code-right-collapsed') === '1') seed['code-right'] = { side: 'right', label: 'Tasks' }
    } catch {   }
    return seed
  })
  useEffect(() => {
    const onCollapsed = (e: Event) => {
      const d = (e as CustomEvent).detail as { panelKey: string; side: 'left' | 'right'; label: string; collapsed: boolean }
      setCollapsedPanels((prev) => {
        const next = { ...prev }
        if (d.collapsed) next[d.panelKey] = { side: d.side, label: d.label }
        else delete next[d.panelKey]
        return next
      })
    }
    window.addEventListener('ne:code-panel-collapsed', onCollapsed as EventListener)
    return () => window.removeEventListener('ne:code-panel-collapsed', onCollapsed as EventListener)
  }, [])
  const collapsedLeft = Object.entries(collapsedPanels).filter(([, v]) => v.side === 'left')
  const collapsedRight = Object.entries(collapsedPanels).filter(([, v]) => v.side === 'right')
  const reopenPanel = (key: string) => window.dispatchEvent(new CustomEvent('ne:code-expand-panel', { detail: key }))
  const saveFileAsArtifact = useCallback((entry: FsEntry, content: string) => {
    const base = entry.name || entry.path.split('/').pop() || 'file'
    const ext = base.includes('.') ? base.split('.').pop()!.toLowerCase() : ''
    const kind = ext === 'md' ? 'markdown' : ext === 'json' ? 'json'
      : ext === 'svg' ? 'svg' : 'text'
    api.createArtifact({ name: base, content, source: 'manual', source_path: entry.path, kind })
      .then(() => window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'ok', text: `Saved “${base}” as an artifact.` } })))
      .catch((e) => window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't save artifact: ${(e as Error).message || 'unknown error'}` } })))
  }, [])
  useEffect(() => {
    if (!ws) return
    const wsBase = ws.replace(/\/$/, '').split('/').pop() || ''
    const underWs = (p: string) => p.startsWith(ws) || (!!wsBase && p.includes(`/${wsBase}/`))
    for (const t of tabs) {
      if (!underWs(t.path)) { draftStore.delete(t.path); closeNow(t.path) }
    }
  }, [ws, tabs, closeNow])
  const canonPath = useCallback((p: string): string => {
    if (!ws || p.startsWith(ws)) return p
    const wsBase = ws.replace(/\/$/, '').split('/').pop() || ''
    const marker = `/${wsBase}/`
    const i = wsBase ? p.lastIndexOf(marker) : -1
    return i >= 0 ? `${ws.replace(/\/$/, '')}/${p.slice(i + marker.length)}` : p
  }, [ws])
  const [diff, setDiff] = useState<{ path: string; name: string; deleted?: boolean } | null>(null)
  const [commit, setCommit] = useState<{ hash: string; subject: string } | null>(null)
  const { mode } = useMode()
  const [reveal, setReveal] = useState<
    | { path: string; name: string; kind: 'write'; text: string }
    | { path: string; name: string; kind: 'diff'; oldText: string; newText: string }
    | { path: string; name: string; kind: 'erase'; text: string }
    | null
  >(null)
  const lastContentRef = useRef<Map<string, string>>(new Map())
  const editorShowing = !reveal && !commit && !diff && !!active
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (document.activeElement as HTMLElement | null)?.closest('.xterm')) return
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's' && editorShowing) {
        e.preventDefault(); viewerRef.current?.save()
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'w' && active) {
        e.preventDefault(); requestClose(active.path, active.name)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [editorShowing, active, requestClose])
  useEffect(() => {
    const onOpen = (e: Event) => {
      const raw = (e as CustomEvent).detail as FsEntry & { _follow?: boolean }
      if (!raw || raw.is_dir) return
      const entry = { ...raw, path: canonPath(raw.path) }
      setDiff(null); setCommit(null)
      if (!entry._follow) setReveal(null)
      if (entry._follow) {
        const prev = lastContentRef.current.get(entry.path)
        api.fileRead(entry.path, true).then((r) => {
          const text = r.content ?? ''
          if (r.binary || r.truncated || text.length > REVEAL_MAX_CHARS) {
            lastContentRef.current.set(entry.path, text)
            open(entry)
            return
          }
          if (prev === undefined) {
            if (text.trim()) { lastContentRef.current.set(entry.path, text); setReveal({ path: entry.path, name: entry.name, kind: 'write', text }) }
            else { lastContentRef.current.set(entry.path, text); open(entry) }
          } else if (prev !== text && text.trim()) {
            lastContentRef.current.set(entry.path, text)
            setReveal({ path: entry.path, name: entry.name, kind: 'diff', oldText: prev, newText: text })
          } else {
            open(entry)
          }
        }).catch(() => open(entry))
        return
      }
      if (lastContentRef.current.get(entry.path) === undefined) {
        api.fileRead(entry.path, true).then((r) => {
          if (!r.binary && !r.truncated && typeof r.content === 'string') {
            lastContentRef.current.set(entry.path, r.content)
          }
        }).catch(() => {})
      }
      open(entry)
    }
    const onDiff = (e: Event) => { const d = (e as CustomEvent).detail as { path: string; name: string; deleted?: boolean }; if (d?.path) { setReveal(null); setCommit(null); setDiff(d) } }
    const onCommit = (e: Event) => { const c = (e as CustomEvent).detail as { hash: string; subject: string }; if (c?.hash) { setReveal(null); setDiff(null); setCommit(c) } }
    const onErase = (e: Event) => {
      const raw = (e as CustomEvent).detail as { path: string; name: string; text?: string }
      const d = raw?.path ? { ...raw, path: canonPath(raw.path) } : raw
      const text = d?.text || lastContentRef.current.get(d?.path)
      if (!d?.path || !text || !text.trim() || text.length > REVEAL_MAX_CHARS) {
        if (d?.path) { lastContentRef.current.delete(d.path); draftStore.delete(d.path); closeNow(d.path) }
        return
      }
      setDiff(null); setCommit(null)
      lastContentRef.current.delete(d.path)
      setReveal({ path: d.path, name: d.name, kind: 'erase', text })
    }
    const onClose = (e: Event) => {
      const p = (e as CustomEvent).detail?.path as string | undefined
      if (!p) return
      const cp = canonPath(p)
      for (const t of tabsRef.current) {
        if (t.path === p || t.path === cp || t.path.startsWith(cp.replace(/\/$/, '') + '/') || t.path.startsWith(p.replace(/\/$/, '') + '/')) {
          draftStore.delete(t.path)
          closeNow(t.path)
        }
      }
    }
    const onSavedFile = (e: Event) => {
      const d = (e as CustomEvent).detail as { path?: string; content?: string } | undefined
      if (d?.path && typeof d.content === 'string') lastContentRef.current.set(canonPath(d.path), d.content)
    }
    const onRenamed = (e: Event) => {
      const d = (e as CustomEvent).detail as { from?: string; to?: string; isDir?: boolean } | undefined
      if (!d?.from || !d?.to) return
      const from = canonPath(d.from), to = canonPath(d.to)
      const fromPfx = from.replace(/\/$/, '') + '/'
      const wasActive = activePathRef.current
      for (const t of tabsRef.current) {
        let next: string | null = null
        if (t.path === from || t.path === d.from) next = to
        else if (t.path.startsWith(fromPfx)) next = to.replace(/\/$/, '') + '/' + t.path.slice(fromPfx.length)
        else if (t.path.startsWith(d.from.replace(/\/$/, '') + '/')) next = to.replace(/\/$/, '') + '/' + t.path.slice(d.from.replace(/\/$/, '').length + 1)
        if (!next) continue
        const wasThisActive = t.path === wasActive
        closeNow(t.path)
        const base = lastContentRef.current.get(t.path)
        if (base !== undefined) { lastContentRef.current.set(next, base); lastContentRef.current.delete(t.path) }
        const d2 = draftStore.get(t.path)
        if (d2 !== undefined) { draftStore.set(next, d2); draftStore.delete(t.path) }
        open({ name: next.split('/').pop() || next, path: next, is_dir: false } as FsEntry)
        if (!wasThisActive) setActivePath(wasActive)
      }
    }
    window.addEventListener('ne:code-open-file', onOpen as EventListener)
    window.addEventListener('ne:code-open-diff', onDiff as EventListener)
    window.addEventListener('ne:code-open-commit', onCommit as EventListener)
    window.addEventListener('ne:code-erase-file', onErase as EventListener)
    window.addEventListener('ne:code-close-file', onClose as EventListener)
    window.addEventListener('ne:code-file-saved', onSavedFile as EventListener)
    window.addEventListener('ne:code-renamed', onRenamed as EventListener)
    return () => {
      window.removeEventListener('ne:code-open-file', onOpen as EventListener)
      window.removeEventListener('ne:code-open-diff', onDiff as EventListener)
      window.removeEventListener('ne:code-open-commit', onCommit as EventListener)
      window.removeEventListener('ne:code-erase-file', onErase as EventListener)
      window.removeEventListener('ne:code-file-saved', onSavedFile as EventListener)
      window.removeEventListener('ne:code-close-file', onClose as EventListener)
      window.removeEventListener('ne:code-renamed', onRenamed as EventListener)
    }
  }, [open, closeNow, canonPath])

  const tabLabels = (() => {
    const counts: Record<string, number> = {}
    for (const t of tabs) counts[t.name] = (counts[t.name] ?? 0) + 1
    const label: Record<string, string> = {}
    for (const t of tabs) {
      if (counts[t.name] > 1) {
        const parent = t.path.replace(/\/$/, '').split('/').slice(-2, -1)[0]
        label[t.path] = parent ? `${t.name} — ${parent}` : t.name
      } else label[t.path] = t.name
    }
    return label
  })()
  const activeTabRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    activeTabRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [activePath])
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      {
}
      {(tabs.length > 0 || collapsedLeft.length > 0 || collapsedRight.length > 0) && (
        <div className="flex shrink-0 items-center gap-0.5 border-b border-outline-variant/40 bg-surface-low/40 px-1">
          { }
          {collapsedLeft.map(([key, v]) => (
            <Button key={key} variant="ghost" size="xs" onClick={() => reopenPanel(key)}
              title={`Show ${v.label}`} ariaLabel={`Show ${v.label}`} className="mr-0.5 shrink-0">
              <PanelLeftOpen size={14} /> {v.label}
            </Button>
          ))}
          <div className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto">
          {tabs.map((t) => (
            <div key={t.path} ref={(!diff && !commit && t.path === activePath) ? activeTabRef : undefined}
              onAuxClick={(e) => { if (e.button === 1) { e.preventDefault(); requestClose(t.path, t.name) } }}
              data-type="body-s" className={`group flex shrink-0 items-center gap-1.5 rounded-t-md px-2.5 py-1.5 ${!diff && !commit && t.path === activePath ? 'bg-surface text-on-surface' : 'text-on-surface-low hover:text-on-surface'}`}>
              {
}
              <button type="button" onClick={() => { setDiff(null); setCommit(null); setActivePath(t.path) }} title={t.path} className="max-w-[180px] truncate">{tabLabels[t.path]}</button>
              {
}
              {dirty[t.path] ? (
                <button type="button" onClick={() => requestClose(t.path, t.name)} aria-label={`Close ${t.name} (unsaved changes)`} title="Unsaved changes" className="shrink-0">
                  <span className="size-2 rounded-full group-hover:hidden" style={{ background: 'var(--color-primary)' }} />
                  <X size={12} className="hidden group-hover:block" />
                </button>
              ) : (
                <IconButton icon={X} label={`Close ${t.name}`} onClick={() => requestClose(t.path, t.name)}
                  size={18} iconSize={12}
                  className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100" />
              )}
            </div>
          ))}
          </div>
          { }
          {collapsedRight.map(([key, v]) => (
            <Button key={key} variant="ghost" size="xs" onClick={() => reopenPanel(key)}
              title={`Show ${v.label}`} ariaLabel={`Show ${v.label}`} className="ml-0.5 shrink-0">
              <PanelRightOpen size={14} /> {v.label}
            </Button>
          ))}
        </div>
      )}
      <div className="min-h-0 flex-1">
        {reveal ? (
          <div className="flex h-full flex-col">
            <div data-type="caption" className="flex shrink-0 items-center gap-1.5 border-b border-outline-variant/40 bg-surface-low/40 px-2 py-1 text-on-surface-low">
              <Loader2 size={11} className="animate-spin text-primary" /> {reveal.kind === 'diff' ? 'Editing' : reveal.kind === 'erase' ? 'Deleting' : 'Writing'} {reveal.name}…
            </div>
            <div className="min-h-0 flex-1">
              {
}
              {reveal.kind === 'diff'
                ? <DiffReveal key={`d:${reveal.path}`} oldText={reveal.oldText} newText={reveal.newText} theme={mode === 'light' ? 'light' : 'dark'}
                    onDone={() => { open({ name: reveal.name, path: reveal.path, is_dir: false } as FsEntry); setReveal(null) }} />
                : reveal.kind === 'erase'
                ? <TypingReveal key={`e:${reveal.path}`} text={reveal.text} mode="erase" theme={mode === 'light' ? 'light' : 'dark'}
                    onDone={() => { closeNow(reveal.path); setReveal(null) }} />
                : <TypingReveal key={`w:${reveal.path}`} text={reveal.text} mode="write" theme={mode === 'light' ? 'light' : 'dark'}
                    onDone={() => { open({ name: reveal.name, path: reveal.path, is_dir: false } as FsEntry); setReveal(null) }} />}
            </div>
          </div>
        ) : commit ? (
          <CommitView ws={ws} hash={commit.hash} subject={commit.subject} onClose={() => setCommit(null)} />
        ) : diff ? (
          <div className="flex h-full flex-col">
            <div className="flex shrink-0 items-center justify-between border-b border-outline-variant/40 bg-surface-low/40 px-2 py-1">
              <span data-type="caption" className="inline-flex items-center gap-1.5 text-on-surface-low"><GitBranch size={12} /> Diff — {diff.name}</span>
              <IconButton icon={X} label="Close diff" onClick={() => setDiff(null)} size={24} iconSize={13} />
            </div>
            <div className="min-h-0 flex-1"><DiffView path={diff.path} name={diff.name} ws={ws} deleted={diff.deleted} /></div>
          </div>
        ) : active ? (
          <FileViewer key={active.path} ref={viewerRef} entry={{ name: active.name, path: active.path, is_dir: false }} draftStore={draftStore}
            onSaved={(content) => window.dispatchEvent(new CustomEvent('ne:code-file-saved', { detail: { path: active.path, content } }))} onSaveAsArtifact={saveFileAsArtifact}
            onMissing={(p) => closeNow(p)}
            onDirtyChange={(d) => { markDirty(active.path, d); if (d) window.dispatchEvent(new CustomEvent('ne:code-editing')) }} />
        ) : (
          <Centered>
            <div className="flex flex-col items-center gap-2 text-on-surface-low">
              <Code2 size={26} className="opacity-40" />
              <p data-type="body-s">Open a file from the tree to view + edit it.</p>
              {
}
              {running && (
                <Button variant="ghost" size="xs" className="mt-1"
                  onClick={() => window.dispatchEvent(new CustomEvent('ne:code-follow-worker'))}>
                  <Activity size={12} /> Follow the worker
                </Button>
              )}
            </div>
          </Centered>
        )}
      </div>
      {
}
      {termOpened && ws && <BottomTerminal ws={ws} hidden={!showTerm} onClose={closeTerm} runCmd={runCmd} />}
    </div>
  )
}

function CommitView({ ws, hash, subject, onClose }: { ws: string; hash: string; subject: string; onClose: () => void }) {
  const [diff, setDiff] = useState<string | null>(null)
  const [truncated, setTruncated] = useState(false)
  const [notFound, setNotFound] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let alive = true
    setDiff(null)
    setTruncated(false)
    setNotFound(false)
    api.fileGitCommit(ws, hash).then((r) => {
      if (!alive) return
      if (r.found === false) { setNotFound(true); setDiff('') }
      else { setDiff(r.diff || ''); setTruncated(!!r.truncated) }
    }).catch(() => { if (alive) setDiff('ERR') })
    return () => { alive = false }
  }, [ws, hash, attempt])
  const failed = diff === 'ERR'
  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-outline-variant/40 bg-surface-low/40 px-2 py-1">
        <span data-type="caption" className="inline-flex min-w-0 items-center gap-1.5 text-on-surface-low">
          <GitBranch size={12} className="shrink-0" /> <span className="font-mono">{hash}</span> <span className="truncate text-on-surface-var">{subject}</span>
        </span>
        <IconButton icon={X} label="Close commit" onClick={onClose} size={24} iconSize={13} className="shrink-0" />
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-surface p-2">
        {diff === null ? (
          <Centered><Loader2 size={16} className="animate-spin text-on-surface-low" /></Centered>
        ) : failed ? (
          <Centered>
            <div data-type="body-s" className="flex flex-col items-center gap-2 px-4 text-center">
              <FieldError>Couldn't load this commit.</FieldError>
              <Button variant="ghost-accent" size="xs" onClick={() => setAttempt((n) => n + 1)}><RotateCcw size={13} /> Try again</Button>
            </div>
          </Centered>
        ) : notFound ? (
          <Centered><p data-type="body-s" className="px-4 text-center text-on-surface-low">This commit is no longer in the workspace — its history may have been rewritten (rebase / force-push) or the workspace re-pointed.</p></Centered>
        ) : diff === '' ? (
          <Centered><p data-type="body-s" className="px-4 text-center text-on-surface-low">This commit has no textual changes (e.g. a merge or an empty checkpoint).</p></Centered>
        ) : (
          <>
            {
}
            <UnifiedDiff patch={diff} label={`Diff for commit ${hash}`} />
            {truncated && (
              <p data-type="caption" className="mt-2 px-1 text-on-surface-low/80">
                Diff truncated — this commit is large; only the first part is shown. Use the workspace terminal (<span className="font-mono">git show {hash}</span>) for the full patch.
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function BottomTerminal({ ws, hidden, onClose, runCmd }: { ws: string; hidden?: boolean; onClose: () => void; runCmd?: { cmd: string; n: number } | null }) {
  const [tab, setTab] = useState<TermTab | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const { width: height, onHandleDown, onHandleKey, min, max } = useResizablePanel(
    'code-term-h', { def: 260, min: 120, max: 640, side: 'bottom', collapsible: true })
  const liveSessionRef = useRef<string>('')
  useEffect(() => {
    let alive = true
    setErr(null); setTab(null)
    api.createTerminal(ws).then((r) => {
      liveSessionRef.current = r.session_id
      if (alive) setTab({ id: r.session_id, label: 'Terminal', cwd: r.cwd, shell: r.shell })
      else api.deleteTerminal(r.session_id).catch(() => {})
    }).catch((e) => { if (alive) setErr((e as Error).message || 'Could not start a terminal here.') })
    return () => { alive = false; if (liveSessionRef.current) api.deleteTerminal(liveSessionRef.current).catch(() => {}) }
  }, [ws, attempt])
  useEffect(() => {
    if (!runCmd || !tab) return
    return runInTerminalWhenReady(runCmd.cmd, () => liveSessionRef.current || tab.id)
  }, [runCmd?.n, tab?.id])  // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="relative flex shrink-0 flex-col border-t border-outline-variant/40"
      style={{ height, display: hidden ? 'none' : undefined }} aria-hidden={hidden}>
      { }
      <div onPointerDown={onHandleDown} onKeyDown={onHandleKey} role="separator" aria-orientation="horizontal"
        tabIndex={0} aria-label="Resize terminal — arrow keys to resize"
        aria-valuenow={Math.round(height)} aria-valuemin={min} aria-valuemax={max}
        className="group/th absolute inset-x-0 top-0 z-20 h-2 cursor-row-resize outline-none focus-visible:bg-primary/30">
        <div className="mx-auto h-0.5 w-full bg-transparent transition-colors group-hover/th:bg-primary/60 group-focus-visible/th:bg-primary" />
      </div>
      <div className="flex shrink-0 items-center justify-between border-b border-outline-variant/40 bg-surface-low/40 px-3 py-1">
        <span data-type="caption" className="inline-flex items-center gap-1.5 text-on-surface-low"><TerminalSquare size={12} /> Terminal · {ws.split('/').slice(-1)[0]}</span>
        <IconButton icon={X} label="Hide terminal" onClick={onClose} size={24} iconSize={13} />
      </div>
      <div className="min-h-0 flex-1">
        {err ? (
          <div data-type="body-s" className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
            <span style={{ color: 'var(--color-danger)' }}>{err}</span>
            <div className="flex items-center gap-2">
              <Button variant="ghost-accent" size="xs" onClick={() => setAttempt((n) => n + 1)}><RotateCcw size={13} /> Try again</Button>
              <Button variant="ghost" size="xs" onClick={onClose}>Close</Button>
            </div>
          </div>
        ) : tab ? <TerminalView tab={tab} onExited={() => {}} onClose={onClose}
            onSession={(sid) => { liveSessionRef.current = sid }} /> : <Centered><Loader2 size={16} className="animate-spin text-on-surface-low" /></Centered>}
      </div>
    </div>
  )
}


function OutcomeBanner({ project: p, findings }: { project: CodeProject; findings: CodeFinding[] }) {
  const TERMINAL: Record<string, { label: string; tone: string; ok: boolean }> = {
    complete: { label: 'Project complete', tone: 'var(--color-ok)', ok: true },
    failed: { label: 'Project failed — fix the issue + Resume to retry', tone: 'var(--color-danger)', ok: false },
    stopped: { label: 'Project stopped', tone: 'var(--color-on-surface-low)', ok: false },
  }
  let meta = TERMINAL[p.status]
  if (!meta) return null
  const incompleteFinish = effectiveLoopStatus(p.status, p.stop_reason) === 'ended_early'
  if (incompleteFinish) {
    meta = { label: 'Project ended before finishing', tone: 'var(--color-warn)', ok: false }
  }
  const stages = p.stage_plan?.length ?? 0
  const stagesDone = Object.values(p.stage_status ?? {}).filter((s) => s === 'done').length
  const cycles = p.total_cycles || findings.length
  const ws = (p.workspace_dir || p.files_dir || '').replace(/\/$/, '')
  const isProjectDir = !p.workspace_dir
  const root = (ws || '').replace(/\/$/, '')
  const files = new Set<string>()
  for (const f of findings) for (const raw of (f.files_touched ?? [])) {
    const r = resolveTouchedPath(raw, root)
    if (!r) continue
    if (isProjectDir) {
      const top = r.rel.split('/')[0]
      if (PROJECT_DIR_HIDDEN.has(top) || [...PROJECT_DIR_HIDDEN_PREFIXES].some((pre) => top.startsWith(pre))) continue
    }
    files.add(r.abs)
  }
  const bits = [
    stages > 0 ? `${stagesDone}/${stages} stages` : '',
    cycles > 0 ? `${cycles} cycle${cycles === 1 ? '' : 's'}` : '',
    files.size > 0 ? `${files.size} file${files.size === 1 ? '' : 's'}` : '',
  ].filter(Boolean)
  const Icon = meta.ok ? CheckCircle2 : XCircle
  return (
    <div data-type="body-s" className="mb-3 rounded-lg p-2.5"
      style={{ background: `color-mix(in srgb, ${meta.tone} 12%, transparent)` }}>
      <div className="inline-flex items-center gap-1.5" style={withWeight({ color: meta.tone }, 550)}>
        <Icon size={14} /> {meta.label}
      </div>
      {bits.length > 0 && <p data-type="caption" className="mt-1 text-on-surface-var">{bits.join(' · ')}</p>}
      {!meta.ok && p.error_message && <p data-type="caption" className="mt-1 text-on-surface-low">{p.error_message}</p>}
      {
}
      {files.size > 0 && (
        <div className="mt-1.5">
          {
}
          <p data-type="caption" className="text-on-surface-low">What was built — click to open:</p>
          <FilesTouched files={[...files]} ws={ws} max={12} />
        </div>
      )}
    </div>
  )
}

function evidenceToText(ev: unknown): string {
  if (ev == null) return ''
  if (typeof ev === 'string') return ev.trim()
  if (Array.isArray(ev)) return ev.map((v) => evidenceToText(v)).filter(Boolean).join('\n')
  if (typeof ev === 'object') {
    return Object.entries(ev as Record<string, unknown>)
      .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join('\n').trim()
  }
  return String(ev)
}

function FindingCard({ finding: f, ws }: { finding: CodeFinding; ws: string }) {
  const [showEvidence, setShowEvidence] = useState(false)
  const evidence = evidenceToText(f.evidence)
  return (
    <div className="rounded-lg border border-outline-variant/40 bg-surface-container/50 p-2.5">
      <div data-type="caption" className="mb-1 flex items-center gap-1.5 text-on-surface-low">
        <span className="rounded-pill bg-surface-high px-1.5 tabular-nums">cycle {f.cycle}</span>
        {f.stage && <span className="rounded-pill bg-surface-high px-1.5">{f.stage}</span>}
      </div>
      {f.summary && <p data-type="body-s" className="text-on-surface-var">{f.summary}</p>}
      {f.key_insight && <p data-type="caption" className="mt-1 text-on-surface-low">→ {f.key_insight}</p>}
      <FilesTouched files={f.files_touched} ws={ws} />
      {evidence && (
        <div className="mt-1.5">
          <button type="button" onClick={() => setShowEvidence((v) => !v)} aria-expanded={showEvidence}
            aria-label={showEvidence ? 'Hide evidence' : 'Show evidence'}
            data-type="caption" className="inline-flex items-center gap-1 text-on-surface-low hover:text-on-surface">
            {showEvidence ? <ChevronDown size={11} /> : <ChevronRight size={11} />} evidence
          </button>
          {showEvidence && (
            <pre data-type="caption" className="mt-1 max-h-48 overflow-auto rounded-md bg-surface-high/60 p-2 text-on-surface-var whitespace-pre-wrap break-words">{evidence}</pre>
          )}
        </div>
      )}
    </div>
  )
}

function ProjectFooter({ project, gateFail, stalled, onNudged, onStartNew }: { project: CodeProject; gateFail: { label: string; command: string; output: string } | null; stalled: { stage: string; title: string; findings: number } | null; onNudged: () => void; onStartNew?: () => void }) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const sendingRef = useRef(false)
  const steerRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { autoGrowTextarea(steerRef.current) }, [text])
  useEffect(() => {
    const onFocus = () => { steerRef.current?.focus(); steerRef.current?.scrollIntoView({ block: 'nearest' }) }
    window.addEventListener('ne:code-focus-steer', onFocus)
    return () => window.removeEventListener('ne:code-focus-steer', onFocus)
  }, [])
  const [steers, setSteers] = useState<{ text: string; failed?: boolean }[]>([])
  const findings = project.findings ?? []

  async function steer(explicit?: string) {
    const t = (explicit ?? text).trim()
    if (!t || sending || sendingRef.current) return
    sendingRef.current = true
    setSending(true)
    setSteers((s) => [...s, { text: t }])
    if (explicit === undefined) setText('')
    try { await api.uLoopNudge(project.id, t); onNudged() }
    catch {
      setSteers((s) => s.map((m, i) => (i === s.length - 1 ? { ...m, failed: true } : m)))
      if (explicit === undefined) setText((cur) => cur || t)
    }
    finally { setSending(false); sendingRef.current = false }
  }

  const lastSteer = (() => {
    const persisted = project.nudges ?? []
    const lastOptimistic = steers[steers.length - 1]
    const succeededCount = steers.filter((s) => !s.failed).length
    if (lastOptimistic && (lastOptimistic.failed || succeededCount > persisted.length)) return lastOptimistic
    return persisted[persisted.length - 1] || lastOptimistic || null
  })()

  return (
    <div className="shrink-0 border-t border-outline-variant/40">
      <div className="max-h-[40vh] overflow-y-auto px-2 pt-2">
        { }
        {project.status === 'needs_input' && project.pending_question?.question && (
          <div role="alert" data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-info) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-info)' }, 550)}>
              <HelpCircle size={14} /> The worker needs your input
            </div>
            <p className="whitespace-pre-wrap text-on-surface">{project.pending_question.question}</p>
            {
}
            {project.pending_question.why && (
              <p data-type="caption" className="mt-1 whitespace-pre-wrap text-on-surface-low">{project.pending_question.why}</p>
            )}
            <div className="mt-2 flex items-center gap-2">
              <p data-type="caption" className="flex-1 text-on-surface-low">Answer below to resume the build.</p>
              { }
              <Button variant="ghost" size="xs" disabled={sending} disabledReason={BUSY_REASON}
                onClick={() => steer('Proceed with your best judgment / the sensible default you proposed. Record the assumption in your finding and continue.')}
                className="shrink-0 px-2 text-[0.75rem] text-info hover:bg-info/10">
                Use your best judgment
              </Button>
            </div>
          </div>
        )}
        {
}
        {project.status === 'needs_input' && !project.pending_question?.question && (
          <div role="alert" data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-info) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-info)' }, 550)}>
              <HelpCircle size={14} /> The worker is waiting on you
            </div>
            <p data-type="caption" className="text-on-surface-var">It paused for input but didn't leave a specific question. Steer it below with direction (or tell it to use its best judgment), then it resumes.</p>
            <div className="mt-2 flex justify-end">
              { }
              <Button variant="ghost" size="xs" disabled={sending} disabledReason={BUSY_REASON}
                onClick={() => steer('Proceed with your best judgment / the sensible default. Record any assumption in your finding and continue.')}
                className="shrink-0 px-2 text-[0.75rem] text-info hover:bg-info/10">
                Use your best judgment
              </Button>
            </div>
          </div>
        )}
        <OutcomeBanner project={project} findings={findings} />
        {
}
        {project.status === 'blocked' && project.error_message && (
          <div data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-warn)' }, 550)}>
              <AlertTriangle size={14} /> Paused — needs you
            </div>
            <p className="whitespace-pre-wrap text-on-surface-var">{project.error_message}</p>
            <p data-type="caption" className="mt-1 text-on-surface-low">Steer it below (or relax a stage criterion), then Resume.</p>
          </div>
        )}
        {gateFail && project.status === 'running' && (
          <div role="alert" data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-warn)' }, 550)}>
              <XCircle size={14} /> Supervisor {gateFail.label} check failed — stage held
            </div>
            {gateFail.command && <p data-type="caption" className="font-mono text-on-surface-low">{gateFail.command}</p>}
            {gateFail.output && (
              <pre data-type="caption" className="mt-1 max-h-40 overflow-auto rounded-md bg-surface-high/60 p-2 text-on-surface-var whitespace-pre-wrap break-words">{gateFail.output}</pre>
            )}
          </div>
        )}
        {
}
        {stalled && !gateFail && (project.status === 'running' || project.status === 'blocked') && (
          <div role="status" data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-warn)' }, 550)}>
              <AlertTriangle size={14} /> “{stalled.title}” {project.status === 'blocked' ? 'is stuck — paused for you' : 'seems stuck'}
            </div>
            <p data-type="caption" className="text-on-surface-var">{stalled.findings} cycles in and the gate still hasn't passed — steer it or relax a criterion{project.status === 'blocked' ? ', then Resume' : ''}.</p>
          </div>
        )}
        { }
        {lastSteer && (
          <div data-type="body-s" className="mb-2 self-end rounded-xl bg-primary/15 px-2.5 py-1.5 text-on-surface-var">
            {lastSteer.text}
            {'applied_cycle' in lastSteer && (lastSteer as { applied_cycle?: number }).applied_cycle != null && (
              <span data-type="caption" className="ml-1.5 text-on-surface-low">· applied cycle {(lastSteer as { applied_cycle?: number }).applied_cycle}</span>
            )}
            {'failed' in lastSteer && (lastSteer as { failed?: boolean }).failed && <span data-type="caption" className="ml-1.5 text-danger">· failed to send</span>}
          </div>
        )}
      </div>
      { }
      <div className="p-2">
        {STEERABLE.has(project.status) ? (
          <div className="flex items-end gap-1.5 rounded-xl bg-surface-container px-2.5 py-1.5 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
            <textarea ref={steerRef} value={text} onChange={(e) => setText(e.target.value)} rows={1}
              placeholder={project.status === 'needs_input' ? 'Answer the worker…' : 'Steer the worker…'}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); steer() } }}
              data-type="body-s" className="max-h-24 min-h-0 flex-1 resize-none overflow-y-auto bg-transparent text-on-surface outline-none placeholder:text-on-surface-low" />
            {
}
            <IconButton icon={Send} label="Send steer" filled size={28} iconSize={13}
              disabled={!text.trim()} disabledReason={!text.trim() ? 'Type a steer first' : undefined}
              loading={sending} onClick={() => steer()} className="shrink-0" />
          </div>
        ) : project.status === 'ready' || project.status === 'review' ? (
          <p data-type="caption" className="px-1.5 py-1 text-center text-on-surface-low">Press Start to launch — steer the worker once it's running.</p>
        ) : (
          <div className="flex flex-col items-center gap-1.5 px-1.5 py-1 text-center">
            <p data-type="caption" className="text-on-surface-low">This project has finished.</p>
            { }
            <Button variant="tonal" size="xs" onClick={() => onStartNew?.()} className="gap-1.5 px-3 text-[0.75rem]">
              <Plus size={13} /> Start a new project
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

function FilesTouched({ files, ws, max = 8 }: { files?: string[]; ws: string; max?: number }) {
  const [expanded, setExpanded] = useState(false)
  if (!files || !files.length || !ws) return null
  const root = ws.replace(/\/$/, '')
  const seen = new Set<string>()
  const unique = files
    .map((raw) => resolveTouchedPath(raw, root))
    .filter((r): r is { abs: string; rel: string } => !!r && (seen.has(r.abs) ? false : (seen.add(r.abs), true)))
  if (!unique.length) return null
  const open = (path: string) => {
    const name = path.split('/').pop() || path
    window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: { name, path, is_dir: false } }))
  }
  const shown = expanded ? unique : unique.slice(0, max)
  const hidden = unique.length - shown.length
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1">
      {shown.map(({ abs: p, rel }) => {
        return (
          <button key={p} type="button" onClick={() => open(p)} title={rel}
            data-type="caption" className="inline-flex max-w-full items-center gap-1 rounded bg-surface-high px-1.5 py-0.5 text-on-surface-low transition-colors hover:text-primary">
            <FileCode size={10} className="shrink-0" />
            <span className="truncate">{rel}</span>
          </button>
        )
      })}
      {hidden > 0 && (
        <TextLink size="xs" ink="emphasis" onClick={() => setExpanded(true)}
          title={`Show ${hidden} more file${hidden === 1 ? '' : 's'}`}>+{hidden} more</TextLink>
      )}
    </div>
  )
}


function Shell({ title, onBack, children }: { title: string; onBack: () => void; children: React.ReactNode }) {
  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar left={<div className="flex items-center gap-2"><Code2 size={18} className="text-primary" /><span data-type="title-l" className="text-on-surface">{title}</span></div>}
        right={<HeaderActions><HeaderControl icon={ListChecks} label="All projects" onClick={onBack} /></HeaderActions>} />
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  )
}

export type { CodeStage }
