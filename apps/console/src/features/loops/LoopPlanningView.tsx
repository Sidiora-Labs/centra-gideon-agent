import { useMemo } from 'react'
import { ListChecks, Users, GitBranch, Target } from 'lucide-react'
import { PlanningWalkthrough, ArtifactSection, artifactList, artifactStrings, type WalkthroughConfig } from '../../shared/ui/PlanningWalkthrough'
import { api, type Loop } from '../../shared/data/api'
import { DesignStepPreview } from './DesignStepPreview'
import { PlanningArtifactDoc } from './PlanningArtifactDoc'
import type { CommentTarget } from '../../shared/ui/content/commentTarget'

const planningCopy = {
  subtitle: 'shaping the plan with you — approve or comment on each step',
  activityLabel: 'Planner activity',
  activityEmpty: 'Starting the planner… it works out the intent, decomposition, the right agents, and a phased plan — one step at a time.',
  cancel: 'Cancel and edit the task',
}

function planningConfig(loopId: string): WalkthroughConfig {
  return {
    planSessionKey: (id) => `loop-plan-${id}`,
    copy: planningCopy,
    api: {
      getSession: api.uLoopPlanSession,
      start: api.uLoopPlanStart,
      retry: api.uLoopPlanRetry,
      approve: api.uLoopPlanApprove,
      comment: api.uLoopPlanComment,
      edit: api.uLoopPlanEdit,
      isReady: async (id) => {
        try { return (await api.uLoop(id)).status === 'review' } catch { return false }
      },
    },
    renderArtifact: (kind, artifact, commentTarget) => <ArtifactView kind={kind} artifact={artifact} loopId={loopId} commentTarget={commentTarget} />,
  }
}

export function LoopPlanningView({ loopId, onReady, onBack }: {
  loopId: string; onReady: (loop: Loop) => void; onBack: () => void
}) {
  const configuration = useMemo(() => planningConfig(loopId), [loopId])
  const finish = async () => {
    try { const loop = await api.uLoop(loopId); onReady(loop) } catch { /* Readiness is retried by the walkthrough. */ }
  }
  return <PlanningWalkthrough id={loopId} cfg={configuration} onBack={onBack} onReady={finish} />
}

function ArtifactView({ kind, artifact, loopId, commentTarget }: {
  kind: string; artifact: Record<string, unknown>; loopId: string; commentTarget?: CommentTarget
}) {
  const markdown = typeof artifact.markdown === 'string' ? artifact.markdown : ''
  const success = typeof artifact.success_criteria === 'string' ? artifact.success_criteria : ''
  const goals = artifactStrings(artifact.sub_goals)
  const members = artifactList(artifact.roster)
  const phases = artifactList(artifact.execution_plan)
  const overrides = artifact.token_overrides && typeof artifact.token_overrides === 'object' ? artifact.token_overrides as Record<string, unknown> : null
  const sections = [
    {
      key: 'intent', show: kind === 'intent' && Boolean(success), label: 'Definition of done', icon: Target,
      body: <p className="text-on-surface-var">{success}</p>,
    },
    {
      key: 'goals', show: goals.length > 0, label: `Sub-goals (${goals.length})`, icon: ListChecks,
      body: <ol className="ml-4 list-decimal space-y-1 text-on-surface-var">{goals.map((goal, index) => <li key={index}>{goal}</li>)}</ol>,
    },
    {
      key: 'roster', show: members.length > 0, label: `Agent quorum (${members.length})`, icon: Users,
      body: <div className="grid gap-s sm:grid-cols-2">{members.map((member, index) => {
        const heading = String(member.role || member.persona || `Member ${index + 1}`)
        const detail = member.role_hint || member.persona
        return <article key={index} className="rounded-lg border border-outline-variant/20 bg-surface p-m">
          <div className="text-on-surface">{heading}</div>
          {!!detail && <p data-type="caption" className="mt-1 text-on-surface-low">{String(detail)}</p>}
        </article>
      })}</div>,
    },
    {
      key: 'phases', show: phases.length > 0, label: `Execution phases (${phases.length})`, icon: GitBranch,
      body: <ol className="grid gap-s">{phases.map((phase, index) => <li key={index} className="rounded-lg border-l-2 border-primary/40 bg-surface px-m py-m">
        <div className="flex items-center gap-s"><span className="text-on-surface">{index + 1}. {String(phase.role || 'phase')}</span>
          {!!phase.min_cycles && <span data-type="caption" className="rounded-md bg-surface-high px-1.5 text-on-surface-low">≥{String(phase.min_cycles)} cycles</span>}
        </div>
        {!!phase.target && <p data-type="caption" className="mt-1 text-on-surface-low">{String(phase.target)}</p>}
        {!!phase.phase_exit && <p data-type="caption" className="mt-1 text-on-surface-low">Advance when: {String(phase.phase_exit)}</p>}
      </li>)}</ol>,
    },
  ]
  const empty = !markdown && !success && !goals.length && !members.length && !phases.length && !overrides
  return <div data-type="body-s" className="grid gap-m rounded-xl border border-outline-variant/30 bg-surface-high/30 p-m">
    {markdown && <PlanningArtifactDoc markdown={markdown} docId={`plan-${loopId}-${kind}`} label={`${kind} plan`} commentTarget={commentTarget} />}
    {overrides && <DesignStepPreview loopId={loopId} stepKind={kind} overrides={overrides} />}
    {sections.filter((section) => section.show).map(({ key, label, icon: Icon, body }) => <ArtifactSection key={key} icon={<Icon size={13} className="text-primary" />} label={label}>{body}</ArtifactSection>)}
    {empty && <span className="text-on-surface-low">No artifact content.</span>}
  </div>
}
