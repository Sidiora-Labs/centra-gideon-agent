import { useEffect, useState } from 'react'
import { LoopsListPage } from './LoopsListPage'
import type { LoopDraft } from './loopDraft'
import { LoopPlanReview } from './LoopPlanReview'
import { LoopPlanningView } from './LoopPlanningView'
import { LoopCockpitPage } from './LoopCockpitPage'
import { DesignCockpitPage } from './DesignCockpitPage'
import type { RouteProps } from '../../app/shell/useQueryState'
import { api, type Loop } from '../../shared/data/api'
import { invalidateKeys } from '../../shared/data/data'

export function LoopsSection({ sub, navigate, query, setQuery }: RouteProps) {
  const seg = (sub || '').split('/')[0]
  const [review, setReview] = useState<LoopDraft | null>(null)
  const [planningId, setPlanningId] = useState<string | null>(null)

  useEffect(() => {
    if (review && (seg === 'history' || (seg && seg !== review.loopId))) setReview(null)
  }, [seg, review])
  useEffect(() => {
    if (!seg && !review && !planningId) navigate('loop', { replace: true })
  }, [seg, review, planningId, navigate])
  useEffect(() => {
    if (planningId && (seg === 'history' || (seg && seg !== planningId))) setPlanningId(null)
  }, [seg, planningId])

  const [resume, setResume] = useState<{ id: string; status: 'planning' | 'review'; loop: Loop } | null>(null)
  useEffect(() => {
    let alive = true
    const concrete = seg && seg !== 'history'
    if (!concrete || planningId === seg || review?.loopId === seg) { setResume(null); return }
    api.uLoop(seg).then((l) => {
      if (!alive) return
      setResume(l && (l.status === 'planning' || l.status === 'review') ? { id: seg, status: l.status as 'planning' | 'review', loop: l } : null)
    }).catch(() => { if (alive) setResume(null) })
    return () => { alive = false }
  }, [seg, planningId, review])

  const draftFromLoop = (l: Loop): LoopDraft => {
    const kc = (l.kind_config || {}) as Record<string, unknown>
    return {
      loopId: l.id,
      classification: {
        kind: 'goal', execution: l.execution ?? 'solo', roster: l.roster,
        plan: l.plan ?? [], success_criteria: l.success_criteria ?? undefined,
        kind_config: kc,
      } as LoopDraft['classification'],
      rigor: l.intake_rigor || 'grill', agent: '', model: '',
      granularity: (kc.granularity as LoopDraft['granularity']) || 'balanced', attended: !!l.attended,
    }
  }

  const activePlanningId = planningId || (resume?.status === 'planning' ? resume.id : null)
  if (activePlanningId && !review) {
    return <LoopPlanningView loopId={activePlanningId}
      onReady={(l) => { setPlanningId(null); setResume(null); if (l.kind === 'design') navigate(`loops/${l.id}`); else setReview(draftFromLoop(l)) }}
      onBack={() => { setPlanningId(null); setResume(null); navigate('loops') }} />
  }

  const reviewDraft = review || (resume?.status === 'review' && resume.loop.kind !== 'design' ? draftFromLoop(resume.loop) : null)
  if (reviewDraft) {
    return <LoopPlanReview draft={reviewDraft}
      onBack={() => { setReview(null); setResume(null); invalidateKeys('loops'); navigate('loops/history') }}
      onLaunched={(id) => { setReview(null); setResume(null); invalidateKeys('loops'); navigate(`loops/${id}`) }} />
  }

  if (seg === 'history') {
    return <LoopsListPage onCreate={() => navigate('loops')} onOpen={(id) => navigate(`loops/${id}`)} query={query} setQuery={setQuery} />
  }

  if (seg) {
    return <CockpitRouter key={seg} id={seg} navigate={navigate} query={query} setQuery={setQuery} />
  }

  return null
}

function CockpitRouter({ id, navigate, query, setQuery }: { id: string } & Pick<RouteProps, 'navigate' | 'query' | 'setQuery'>) {
  const [kind, setKind] = useState<Loop['kind'] | null>(null)
  const [missing, setMissing] = useState(false)
  useEffect(() => {
    let alive = true
    setKind(null); setMissing(false)
    api.uLoop(id).then((l) => { if (alive) setKind(l?.kind ?? null) })
      .catch(() => { if (alive) setMissing(true) })
    return () => { alive = false }
  }, [id])

  if (missing) {
    return (
      <div data-type="body-s" className="grid h-full place-items-center text-on-surface-low">
        This loop no longer exists.
      </div>
    )
  }
  if (kind === null) {
    return <div data-type="body-s" className="grid h-full place-items-center text-on-surface-low">Loading…</div>
  }
  if (kind === 'design') {
    return <DesignCockpitPage id={id}
      onBack={() => navigate('loops/history')}
      onDeleted={() => { invalidateKeys('loops'); navigate('loops/history') }}
      onOpenProject={(pid) => navigate(`projects/${pid}`)}
      onBuildWithChat={(l) => {
        const seed = `Help me build and refine components for the design system in design loop \`${l.id}\`. `
          + `Read its current tokens via the design token set, and when you create a React component, save it with `
          + `artifact_save(kind='react', tags=['loop:${l.id}']) so it renders live on this loop's canvas. `
          + `Style every component from the design system's token values. First, what component should we build?`
        const pid = l.project_id || l.tasks_project_id || ''
        navigate(`chat/new?project=${encodeURIComponent(pid)}&seed=${encodeURIComponent(seed)}`)
      }}
      query={query} setQuery={setQuery} />
  }
  return <LoopCockpitPage id={id}
    onBack={() => navigate('loops/history')}
    onDeleted={() => { invalidateKeys('loops'); navigate('loops/history') }}
    onOpenArtifact={(slug) => navigate(`artifacts/${slug}`)}
    onOpenTask={(taskId) => navigate(`tasks?open=${encodeURIComponent(taskId)}`)}
    onOpenProject={(pid) => navigate(`projects/${pid}`)}
    query={query} setQuery={setQuery} />
}
