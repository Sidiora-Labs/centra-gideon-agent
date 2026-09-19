import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Check, ListChecks, GitBranch, Boxes, Lightbulb, Loader2, RefreshCw } from 'lucide-react'
import { PlanningWalkthrough, ArtifactSection, artifactList, artifactStrings, type WalkthroughConfig } from '../../shared/ui/PlanningWalkthrough'
import { api, type Loop, type LoopPlanState } from '../../shared/data/api'
import { PlanningArtifactDoc } from '../loops/PlanningArtifactDoc'
import type { CommentTarget } from '../../shared/ui/content/commentTarget'
import { TopBar } from '../../shared/ui/TopBar'
import { Button } from '../../shared/ui/Button'

function makeCfg(projectId: string): WalkthroughConfig {
  return {
    planSessionKey: (id) => `loop-plan-${id}`,
    api: {
      getSession: (id) => api.uLoopPlanSession(id),
      start: (id) => api.uLoopPlanStart(id),
      retry: (id) => api.uLoopPlanRetry(id),
      approve: (id, sid) => api.uLoopPlanApprove(id, sid),
      comment: (id, sid, text) => api.uLoopPlanComment(id, sid, text),
      edit: (id, sid, md) => api.uLoopPlanEdit(id, sid, md),
      isReady: (id) => api.uLoop(id).then((p) => p.status === 'review').catch(() => false),
    },
    copy: {
      subtitle: 'walking the SDLC steps — approve or comment on each',
      activityLabel: 'Investigation',
      activityEmpty: 'Starting the planner… it reads the workspace, fetches relevant docs, and searches as needed before drafting each step.',
      cancel: 'Cancel and edit the task',
    },
    renderArtifact: (kind, artifact, commentTarget) => <ArtifactView kind={kind} artifact={artifact} projectId={projectId} commentTarget={commentTarget} />,
  }
}

export function CodePlanningView({ projectId, onReady, onBack }: {
  projectId: string
  onReady: (project: Loop) => void
  onBack: () => void
}) {
  const { state, checked, retrying, error, retry } = usePlannerState(projectId)

  if (!checked) {
    return <div className="flex h-full items-center justify-center"><Loader2 size={22} className="animate-spin text-primary" /></div>
  }

  if (state?.planner.retryable) {
    return (
      <div className="relative flex h-full flex-col overflow-hidden">
        <TopBar
          left={<div className="flex items-center gap-2"><AlertTriangle size={17} className="text-warn" /><span data-type="title-l" className="text-on-surface">Planning paused</span></div>}
          right={<button type="button" onClick={onBack} data-type="body-s" className="rounded-pill px-3 h-9 text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface">Cancel and edit the task</button>} />
        <main className="min-h-0 flex-1 px-l py-l">
          <PlannerRecoveryNotice retrying={retrying} error={error} onRetry={retry} />
        </main>
      </div>
    )
  }

  return (
    <PlanningWalkthrough id={projectId} cfg={makeCfg(projectId)} onBack={onBack}
      onReady={() => { api.uLoop(projectId).then(onReady).catch(() => {}) }} />
  )
}

function usePlannerState(projectId: string) {
  const [state, setState] = useState<LoopPlanState | null>(null)
  const [checked, setChecked] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try { setState(await api.uLoopPlanState(projectId)) }
    catch { setState(null) }
    finally { setChecked(true) }
  }, [projectId])

  useEffect(() => {
    let alive = true
    const load = async () => {
      try { const next = await api.uLoopPlanState(projectId); if (alive) setState(next) }
      catch { if (alive) setState(null) }
      finally { if (alive) setChecked(true) }
    }
    void load()
    const iv = setInterval(load, 3000)
    return () => { alive = false; clearInterval(iv) }
  }, [projectId])

  const retry = useCallback(async () => {
    if (retrying) return
    setRetrying(true); setError(null)
    try { await api.uLoopPlanRetry(projectId); await refresh() }
    catch (e) { setError(`Couldn't restart planning: ${(e as Error).message || 'unknown error'}`) }
    finally { setRetrying(false) }
  }, [projectId, refresh, retrying])

  return { state, checked, retrying, error, retry }
}

export function PlannerRecoveryNotice({ retrying, error, onRetry }: {
  retrying: boolean
  error: string | null
  onRetry: () => void
}) {
  return (
    <div role="alert" className="mx-auto flex w-full flex-col items-start gap-3 rounded-xl border border-outline-variant/50 bg-surface-container/60 p-4" style={{ maxWidth: 'var(--content-width)' }}>
      <div>
        <p data-type="title-s" className="text-on-surface">The planner is no longer running</p>
        <p data-type="body-s" className="mt-1 text-on-surface-low">The server stopped receiving planner activity. Retry resumes from the saved planning session instead of waiting on this page's clock.</p>
      </div>
      <Button size="sm" variant="tonal" loading={retrying} loadingLabel="Restarting planning…" onClick={() => onRetry()}>
        <RefreshCw size={14} /> Retry planning
      </Button>
      {error && <p data-type="body-s" style={{ color: 'var(--color-danger)' }}>{error}</p>}
    </div>
  )
}

function ArtifactView({ kind, artifact, projectId, commentTarget }: { kind: string; artifact: Record<string, unknown>; projectId: string; commentTarget?: CommentTarget }) {
  const md = typeof artifact.markdown === 'string' ? artifact.markdown : ''
  const keyPoints = artifactStrings(artifact.key_points)
  const stories = artifactStrings(artifact.stories)
  const decisions = artifactList(artifact.decisions)
  const entities = artifactList(artifact.entities)
  const phases = artifactList(artifact.phases)
  const empty = !md && !keyPoints.length && !stories.length && !decisions.length && !entities.length && !phases.length

  return (
    <div data-type="body-s" className="flex flex-col gap-3 rounded-lg bg-surface-high/40 p-3">
      {
}
      {md && <PlanningArtifactDoc markdown={md} docId={`code-plan-${projectId}-${kind}`} label={`${kind} plan`} commentTarget={commentTarget} />}

      {(kind === 'requirements' && stories.length > 0) && (
        <ArtifactSection icon={<ListChecks size={13} className="text-primary" />} label={`User stories (${stories.length})`}>
          <ul className="flex flex-col gap-1">
            {stories.map((s, i) => (
              <li key={i} className="flex items-start gap-1.5 text-on-surface-var">
                <Check size={12} className="mt-0.5 shrink-0 text-on-surface-low" />{s}
              </li>
            ))}
          </ul>
        </ArtifactSection>
      )}

      {decisions.length > 0 && (
        <ArtifactSection icon={<Lightbulb size={13} className="text-primary" />} label={`Decisions (${decisions.length})`}>
          <div className="flex flex-col gap-1.5">
            {decisions.map((d, i) => (
              <div key={i} className="rounded-md bg-surface p-2">
                <div className="text-on-surface">{String(d.title || d.decision || d.name || `Decision ${i + 1}`)}</div>
                {!!(d.rationale || d.why) && <div data-type="caption" className="text-on-surface-low">{String(d.rationale || d.why)}</div>}
              </div>
            ))}
          </div>
        </ArtifactSection>
      )}

      {entities.length > 0 && (
        <ArtifactSection icon={<Boxes size={13} className="text-primary" />} label={`Entities (${entities.length})`}>
          <div className="flex flex-wrap gap-1.5">
            {entities.map((e, i) => (
              <span key={i} data-type="caption" className="rounded-md bg-surface px-2 py-1 text-on-surface-var" title={String(e.description || e.role || '')}>
                {String(e.name || e.title || e.entity || `Entity ${i + 1}`)}
              </span>
            ))}
          </div>
        </ArtifactSection>
      )}

      {phases.length > 0 && (
        <ArtifactSection icon={<GitBranch size={13} className="text-primary" />} label={`Execution phases (${phases.length})`}>
          <div className="flex flex-col gap-2">
            {phases.map((p, i) => {
              const tasks = artifactList(p.tasks)
              const exit = artifactStrings(p.exit_criteria)
              return (
                <div key={i} className="rounded-md bg-surface p-2.5">
                  <div className="flex items-center gap-1.5">
                    <span className="text-on-surface">{i + 1}. {String(p.title || p.stage || 'Phase')}</span>
                    {!!p.stage && <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 text-on-surface-low">{String(p.stage)}</span>}
                  </div>
                  {!!p.objective && <div data-type="caption" className="mt-0.5 text-on-surface-low">{String(p.objective)}</div>}
                  {tasks.length > 0 && (
                    <ol className="mt-1.5 flex flex-col gap-1">
                      {tasks.map((t, j) => {
                        const deps = Array.isArray(t.depends_on) ? (t.depends_on as unknown[]).map((x) => Number(x) + 1).filter((n) => !Number.isNaN(n)) : []
                        return (
                          <li key={j} data-type="caption" className="flex items-start gap-1.5 text-on-surface-var">
                            <span className="mt-0.5 shrink-0 text-on-surface-low">{i + 1}.{j + 1}</span>
                            <span>
                              {String(t.title || `Task ${j + 1}`)}
                              {!!deps.length && <span data-type="caption" className="ml-1 text-on-surface-low">↳ after {deps.map((d) => `${i + 1}.${d}`).join(', ')}</span>}
                            </span>
                          </li>
                        )
                      })}
                    </ol>
                  )}
                  {exit.length > 0 && (
                    <div data-type="caption" className="mt-1.5 text-on-surface-low">Done when: {exit.join('; ')}</div>
                  )}
                </div>
              )
            })}
          </div>
        </ArtifactSection>
      )}

      {
}
      {keyPoints.length > 0 && !(kind === 'requirements' && stories.length > 0) && (
        <ArtifactSection icon={<ListChecks size={13} className="text-primary" />} label="Key points">
          <ul className="ml-4 list-disc text-on-surface-var">{keyPoints.map((k, i) => <li key={i}>{k}</li>)}</ul>
        </ArtifactSection>
      )}

      {empty && <span className="text-on-surface-low">No artifact content.</span>}
    </div>
  )
}
