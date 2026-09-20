import { useEffect, useState } from 'react'
import { Code2, Plus, Loader2, Trash2, FolderOpen, Search, Filter } from 'lucide-react'
import type { CodeDraft } from './codeDraft'
import { codeDeleteBody } from './codeMeta'
import { CodePlanReview } from './CodePlanReview'
import { CodePlanningView } from './CodePlanningView'
import { CodeCockpitPage } from './CodeCockpitPage'
import { TopBar } from '../../shared/ui/TopBar'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { InlineError } from '../../shared/ui/InlineError'
import { ListControls } from '../../shared/ui/ListControls'
import { FilterMenu, type FilterSectionDef } from '../../shared/ui/FilterMenu'
import { EmptyState, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { confirmDelete } from '../../shared/ui/dialog'
import { WorkspacePicker } from './WorkspacePicker'
import { api, sdlcStageLabel, type Loop, type LoopPhase } from '../../shared/data/api'
import { loopStatusLabel, effectiveLoopStatus, loopStatusTone, ACTIVE_LOOP_STATUSES } from '../../shared/data/loopStatus'
import { useVisiblePoll } from '../../shared/data/useVisiblePoll'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import type { RouteProps } from '../../app/shell/useQueryState'

export function CodeSection({ sub, navigate, query, setQuery }: RouteProps) {
  const seg = (sub || '').split('/')[0]
  const [review, setReview] = useState<CodeDraft | null>(null)
  const [planningId, setPlanningId] = useState<string | null>(null)

  useEffect(() => {
    if (!review) return
    if (seg === 'history' || (seg && seg !== review.projectId)) setReview(null)
  }, [seg, review])
  useEffect(() => {
    if (!planningId) return
    if (seg === 'history' || (seg && seg !== planningId)) setPlanningId(null)
  }, [seg, planningId])
  useEffect(() => {
    if (!seg && !review && !planningId) navigate('loop?kind=code', { replace: true })
  }, [seg, review, planningId, navigate])

  const [resume, setResume] = useState<{ id: string; status: 'planning' | 'review'; project: Loop } | null>(null)
  useEffect(() => {
    let alive = true
    const concrete = seg && seg !== 'history'
    if (!concrete || planningId === seg || review?.projectId === seg) { setResume(null); return }
    api.uLoop(seg).then((p) => {
      if (!alive) return
      const planningLike = p && (p.status === 'planning' || p.status === 'intake')
      setResume(p && (planningLike || p.status === 'review')
        ? { id: seg, status: (planningLike ? 'planning' : 'review'), project: p } : null)
    }).catch((e) => {
      if (!alive) return
      if ((e as { status?: number })?.status === 404) setResume(null)
    })
    return () => { alive = false }
  }, [seg, planningId, review])

  const activePlanningId = planningId || (resume?.status === 'planning' ? resume.id : null)

  if (activePlanningId && !review) {
    return <CodePlanningView projectId={activePlanningId}
      onReady={(p) => { setPlanningId(null); setResume(null); setReview({ projectId: p.id, classification: { stage_plan: p.plan, entry_stage: kindCfg(p).entry_stage, summary: p.summary } as unknown as CodeDraft['classification'], rigor: p.intake_rigor || 'grill', attended: !!p.attended }) }}
      onBack={() => { setPlanningId(null); setResume(null); navigate('code') }} />
  }

  const reviewDraft: CodeDraft | null = review || (resume?.status === 'review'
    ? { projectId: resume.id, classification: { stage_plan: resume.project.plan, entry_stage: kindCfg(resume.project).entry_stage, summary: resume.project.summary } as unknown as CodeDraft['classification'], rigor: resume.project.intake_rigor || 'grill', attended: !!resume.project.attended }
    : null)
  if (reviewDraft) {
    return <CodePlanReview draft={reviewDraft}
      onBack={() => { setReview(null); setResume(null); invalidateKeys('code:projects'); navigate('code/history') }}
      onLaunched={(id) => { setReview(null); setResume(null); invalidateKeys('code:projects'); navigate(`code/${id}`) }} />
  }

  if (seg === 'history') {
    return <CodeListPage onCreate={() => navigate('code')} onOpen={(id) => navigate(`code/${id}`)} />
  }

  if (seg) {
    return <CodeCockpitPage key={seg} id={seg}
      onBack={() => navigate('code/history')}
      onDeleted={() => { invalidateKeys('code:projects'); navigate('code/history') }}
      onNewTarget={(ws) => navigate(`loop?kind=code&ws=${encodeURIComponent(ws)}`)}
      onOpenProject={(pid) => navigate(`projects/${pid}`)}
      onStartNew={() => navigate('code')}
      query={query} setQuery={setQuery} />
  }

  return null
}

const kindCfg = (p: Loop): Record<string, unknown> => (p.kind_config || {}) as Record<string, unknown>
const entryStage = (p: Loop): string => String(p.kind_config?.entry_stage ?? '')
const projectKind = (p: Loop): string => String(p.kind_config?.project_kind ?? '')
const stagePlan = (p: Loop): LoopPhase[] => (p.plan ?? []) as LoopPhase[]
const stageStatus = (p: Loop): Record<string, string> => (p.phase_status ?? {}) as Record<string, string>

const effectiveStatus = (p: Loop): string => effectiveLoopStatus(p.status, p.stop_reason)

const statusPill = (status: string): React.CSSProperties => loopStatusTone(status, 18)

const STATUS_RANK: Record<string, number> = {
  needs_input: 0, blocked: 1, stagnant: 2, failed: 3,
  running: 4, planning: 5, intake: 6,
  paused: 7,
  review: 8, ready: 9,
  complete: 10, stopped: 11,
}
const _RANK_FALLBACK = 12
function byAttention(a: Loop, b: Loop): number {
  const ra = STATUS_RANK[a.status] ?? _RANK_FALLBACK, rb = STATUS_RANK[b.status] ?? _RANK_FALLBACK
  if (ra !== rb) return ra - rb
  return (b.created_at ?? 0) - (a.created_at ?? 0)
}

function needsWorkspace(p: Loop): boolean {
  return projectKind(p) === 'brownfield' && !p.workspace_dir && (p.status === 'ready' || p.status === 'review')
}

type CodeFilter = 'active' | 'attention' | 'all' | 'done'
const _ACTIVE_ST = ['running', 'planning', 'intake', 'paused']
const _ATTENTION_ST = ['needs_input', 'blocked', 'stagnant', 'failed', 'review', 'ready']
const _DONE_ST = ['complete', 'stopped']
function matchesFilter(p: Loop, f: CodeFilter): boolean {
  return f === 'all' ? true
    : f === 'active' ? _ACTIVE_ST.includes(p.status)
    : f === 'attention' ? _ATTENTION_ST.includes(p.status)
    : _DONE_ST.includes(p.status)
}

function CodeListPage({ onCreate, onOpen }: { onCreate: () => void; onOpen: (id: string) => void }) {
  const { data: projects, error: loadErr, refresh } = useQuery('code:projects', () => api.uLoops({ kind: 'code' }), { persist: false })
  const load = () => { invalidateKeys('code:projects'); refresh() }
  const [pickFor, setPickFor] = useState<Loop | null>(null)
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState<CodeFilter>('all')
  const [actionErr, setActionErr] = useState<string | null>(null)

  const needle = q.trim().toLowerCase()
  const shown = (projects ?? [])
    .filter((p) => matchesFilter(p, filter))
    .filter((p) => {
      if (!needle) return true
      const es = effectiveStatus(p)
      return `${p.name} ${entryStage(p)} ${sdlcStageLabel(entryStage(p))} ${projectKind(p)} ${es} ${loopStatusLabel(es)}`.toLowerCase().includes(needle)
    })
    .slice().sort(byAttention)
  const codeHasLive = (projects ?? []).some((p) => !['complete', 'stopped', 'failed'].includes(p.status))
  useVisiblePoll(refresh, codeHasLive ? 6000 : null)

  async function del(p: Loop) {
    setActionErr(null)
    if (!(await confirmDelete('project', p.name, { body: codeDeleteBody(p) }))) return
    try { await api.deleteULoop(p.id) }
    catch (e) { setActionErr(`Couldn't delete that project: ${(e as Error).message || 'unknown error'}`) }
    load()
  }

  const progress = (p: Loop) => {
    const plan = stagePlan(p)
    const total = plan.length
    if (!total) return ''
    const ss = stageStatus(p)
    const done = Object.values(ss).filter((s) => s === 'done').length
    const base = `${done}/${total} stages`
    if (!ACTIVE_LOOP_STATUSES.has(p.status) || done >= total) return base
    const active = plan.find((s) => (ss[(String(s.stage ?? '').trim() || String(s.title ?? '').trim())] ?? 'pending') !== 'done')
    const name = active ? String(active.title || active.stage || '') : ''
    return name ? `${base} · ${sdlcStageLabel(name)}` : base
  }

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar
        left={<div className="flex items-center gap-2"><Code2 size={18} className="text-primary" /><span data-type="title-l" className="text-on-surface">Code projects</span></div>}
        right={<HeaderActions><HeaderControl icon={Plus} label="New project" onClick={onCreate} variant="primary" priority="primary" /></HeaderActions>} />
      { }
      {!!projects?.length && (
        <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search projects', label: 'Search projects' }}
          results={{ count: shown.length, noun: 'projects', active: !!needle || filter !== 'all' }}>
          <FilterMenu sections={[{
            title: 'Show', value: filter, defaultKey: 'all',
            onChange: (k) => setFilter(k as CodeFilter),
            options: [
              { key: 'all', label: 'All', count: projects!.length },
              { key: 'active', label: 'Active', count: projects!.filter((p) => matchesFilter(p, 'active')).length },
              { key: 'attention', label: 'Needs you', count: projects!.filter((p) => matchesFilter(p, 'attention')).length },
              { key: 'done', label: 'Done', count: projects!.filter((p) => matchesFilter(p, 'done')).length },
            ],
          } satisfies FilterSectionDef]} />
        </ListControls>
      )}
      {
}
      {actionErr && (
        <InlineError icon className="mx-l mt-2" onDismiss={() => setActionErr(null)}>{actionErr}</InlineError>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto px-l py-l">
        <div className="mx-auto w-full" style={{ maxWidth: 'var(--content-width)' }}>
          {projects === undefined && loadErr ? (
            <LoadError what="projects" error={loadErr} onRetry={load} />
          ) : projects === undefined ? (
            <ListSkeleton rows={6} what="projects" />
          ) : projects.length === 0 ? (
            <EmptyState
              icon={Code2}
              title="No code projects yet"
              hint="Describe an SDLC task — an idea, a spec, a task list, or a bugfix — and an agent will detect its stage, plan the work, and execute it in a workspace."
              action={{ label: 'Start a project', onClick: onCreate, icon: Plus }}
            />
          ) : (() => {
            if (shown.length === 0) return (
              needle ? (
                <EmptyState
                  icon={Search}
                  title={`No projects match “${q.trim()}”`}
                  hint={`You have ${(projects ?? []).length} project${(projects ?? []).length === 1 ? '' : 's'} — just none matching the search.`}
                  action={{ label: 'Clear search', onClick: () => setQ('') }}
                />
              ) : (
                <EmptyState
                  icon={Filter}
                  title="No projects in this view"
                  hint={`You have ${(projects ?? []).length} project${(projects ?? []).length === 1 ? '' : 's'} — just none in this view.`}
                  action={{ label: 'View all projects', onClick: () => setFilter('all') }}
                />
              )
            )
            return (
            <div className="flex flex-col gap-2">
              {shown.map((p) => (
                <div key={p.id} role="button" tabIndex={0} onClick={() => onOpen(p.id)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(p.id) } }}
                  className="group flex cursor-pointer items-center gap-3 rounded-xl border border-outline-variant/50 bg-surface-container/60 px-4 py-3 text-left transition-colors hover:bg-surface-high">
                  <Code2 size={16} className="shrink-0 text-on-surface-low" />
                  <div className="min-w-0 flex-1">
                    <div data-type="body-m" className="truncate text-on-surface">{p.name}</div>
                    <div data-type="caption" className="truncate text-on-surface-low">{sdlcStageLabel(entryStage(p))} · {projectKind(p)}{progress(p) ? ` · ${progress(p)}` : ''}</div>
                  </div>
                  {
}
                  {needsWorkspace(p) && (
                    <button type="button" onClick={(e) => { e.stopPropagation(); setPickFor(p) }}
                      data-type="caption" className="shrink-0 inline-flex items-center gap-xs rounded-pill px-2 py-0.5 transition-colors hover:brightness-110"
                      style={{ background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)', color: 'var(--color-warn)' }}
                      title="Choose a workspace folder before this project can start">
                      <FolderOpen size={11} /> needs workspace
                    </button>
                  )}
                  {(p.status === 'running' || p.status === 'planning' || p.status === 'intake') && <Loader2 size={12} className="shrink-0 animate-spin text-primary" />}
                  {(() => { const es = effectiveStatus(p); return (
                    <span data-type="caption" className="shrink-0 rounded-pill px-2 py-0.5" style={statusPill(es)}
                      title={p.error_message || undefined}>{loopStatusLabel(es)}</span>
                  ) })()}
                  {
}
                  <SquareIconButton icon={Trash2} tone="danger" label="Delete project"
                    onClick={(e) => { e.stopPropagation(); del(p) }}
                    className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100" />
                </div>
              ))}
            </div>
            )
          })()}
        </div>
      </div>
      {pickFor && (
        <WorkspacePicker mode="brownfield" onClose={() => setPickFor(null)}
          onPick={async (dir) => {
            const id = pickFor.id
            setPickFor(null); setActionErr(null)
            try { await api.updateULoop(id, { workspace_dir: dir }) }
            catch (e) { setActionErr(`Couldn't set the workspace: ${(e as Error).message || 'unknown error'}`) }
            load()
          }} />
      )}
    </div>
  )
}
