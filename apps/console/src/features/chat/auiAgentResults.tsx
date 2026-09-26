import { useState } from 'react'
import { AgentPlan } from '../../shared/vendor/assistant-ui/elements/agent-plan'
import { SubagentList } from '../../shared/vendor/assistant-ui/elements/subagent-list'
import { AgentStatus } from '../../shared/vendor/assistant-ui/elements/agent-status'
import { ApprovalCard } from '../../shared/vendor/assistant-ui/elements/approval-card'
import { TaskCard } from '../../shared/vendor/assistant-ui/elements/task-card'
import { RecommendationCard } from '../../shared/vendor/assistant-ui/elements/recommendation-card'
import { ArtifactCard } from '../../shared/vendor/assistant-ui/elements/artifact-card'
import { AgentHandoff } from '../../shared/vendor/assistant-ui/elements/agent-handoff'
import { api, type Artifact, type ChatSession, type JudgeBenchRecommendation, type PendingApproval, type PlanStep, type SpawnedAgent, type WorkflowContinuation, type WorkflowRunDetailData } from '../../shared/data/api'

// Option list is a Gideon composition of the donor TaskCard action slot; the donor has no option-list element.
export const OPTION_LIST_PROVENANCE = 'Gideon composition: assistant-ui elements/task-card.tsx (donor 72e404e)'
export const QUESTION_FLOW_PROVENANCE = 'Gideon composition: assistant-ui elements/task-card.tsx and option-list composition (donor 72e404e)'

export function AgentPlanResult({ steps }: { steps: PlanStep[] }) {
  if (!steps.length) return null
  const completed = steps.filter(step => step.status === 'approved').length
  const linear = steps.slice(0, completed).every(step => step.status === 'approved') &&
    (completed === steps.length || steps[completed].status === 'running')
  return linear
    ? <AgentPlan steps={steps.map(step => step.title)} activeIndex={completed} />
    : <ol aria-label="Agent plan">{steps.map(step => <li key={step.id}>{step.title} — {step.status}</li>)}</ol>
}

export function SubagentResults({ agents }: { agents: SpawnedAgent[] }) {
  const completed = agents.filter(agent => agent.done && !agent.error)
  const other = agents.filter(agent => !agent.done || agent.error)
  return <div>
    {completed.length > 0 && <SubagentList agents={completed.map(agent => ({ name: agent.agent ? `${agent.agent} · ${agent.id}` : agent.id, model: '' }))}
      completedCount={completed.length} progress={completed.map(() => 100)} showSummary={false}
      summaryAgent={{ name: '', model: '' }} />}
    {other.map(agent => <TaskCard key={agent.id} label={agent.agent || agent.id} meta={agent.id}
      state={agent.error ? 'failed' : 'working'} result={agent.error || agent.task} />)}
  </div>
}

export function SessionAgentStatus({ session }: { session: ChatSession }) {
  return <AgentStatus state={session.running ? 'working' : 'waiting'}
    label={`${session.agent}: ${session.pending_approval ? 'approval needed' : session.running ? 'running' : 'idle'}`}
    trailing={null} aria-label={`Agent ${session.agent}, session ${session.key}`} />
}

export function PendingApprovalResult({ approval, onResolved }: { approval: PendingApproval; onResolved: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [decision, setDecision] = useState<'approve' | 'reject' | null>(null)
  async function decide(action: 'approve' | 'reject') {
    setBusy(true)
    setError('')
    try {
      const result = await api.resolveApproval(approval.id, action)
      if (!result.ok) throw new Error('Approval decision was not confirmed')
      setDecision(action)
      onResolved()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }
  return <div aria-label={`Approval ${approval.id}`}>
    {decision ? <p role="status">{decision === 'approve' ? 'Approved' : 'Rejected'}: {approval.tool}</p>
      : <ApprovalCard state="request" command={typeof approval.tool_input === 'string' ? approval.tool_input : JSON.stringify(approval.tool_input ?? {})}
        title={approval.tool} subtitle={approval.tool_purpose || approval.source}
        onAllowOnce={busy ? undefined : () => void decide('approve')}
        onDeny={busy ? undefined : () => void decide('reject')} />}
    {busy && <p role="status">Saving decision…</p>}
    {error && <p role="alert">{error}</p>}
  </div>
}

export interface AgentOption { id: string; label: string; description?: string }
export function AgentOptionList({ title, options, onSelect }: {
  title: string; options: AgentOption[]; onSelect: (id: string) => Promise<{ ok: boolean }>
}) {
  const [busy, setBusy] = useState('')
  const [selected, setSelected] = useState('')
  const [error, setError] = useState('')
  async function choose(id: string) {
    setBusy(id)
    setError('')
    try {
      const result = await onSelect(id)
      if (!result.ok) throw new Error('Selection was not confirmed')
      setSelected(id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy('')
    }
  }
  return <div aria-label={title}>
    <TaskCard label={title} state={selected ? 'done' : 'waiting'}
      actions={options.length ? <div role="group" aria-label={title}>{options.map(option => <button key={option.id} type="button"
        disabled={!!busy || !!selected} onClick={() => void choose(option.id)} title={option.description}>
        {option.label}{selected === option.id ? ' — selected' : ''}
      </button>)}</div> : <span>No options available</span>} />
    {error && <p role="alert">{error}</p>}
  </div>
}

export function WorkflowQuestionFlow({ runId, continuation, onResolved }: {
  runId: string; continuation: WorkflowContinuation; onResolved: () => void
}) {
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const prompt = continuation.ask.prompt
  const choices = continuation.ask.choices
  async function submit(value: string) {
    setBusy(true)
    setError('')
    try {
      const result = await api.resumeWorkflowRun(runId, { answer: value, resume_token: continuation.resume_token })
      if (result.ok !== true && result.resumed !== true) throw new Error('Workflow answer was not confirmed')
      onResolved()
      return { ok: true }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      return { ok: false }
    } finally {
      setBusy(false)
    }
  }
  if (continuation.expired) return <p role="status">Question expired for run {runId}; reopen the workflow to request a new answer.</p>
  if (continuation.ask.kind !== 'choice' && continuation.ask.kind !== 'text')
    return <p role="status">This workflow input must be answered in the run inspector.</p>
  if (!prompt) return <p role="status">Question unavailable for run {runId}.</p>
  if (continuation.ask.kind === 'choice' && !choices?.length)
    return <p role="status">No choices were supplied for run {runId}.</p>
  return <div aria-label={`Question for run ${runId}`}>
    {continuation.ask.kind === 'choice' ? <AgentOptionList title={prompt} options={choices!.map(value => ({ id: value, label: value }))} onSelect={submit} />
      : <TaskCard label={prompt} meta={continuation.node_id} state="waiting"
        actions={<form onSubmit={event => { event.preventDefault(); void submit(answer.trim()) }}>
          <label>Answer <textarea value={answer} onChange={event => setAnswer(event.target.value)} required /></label>
          <button type="submit" disabled={busy || !answer.trim()}>Send answer</button>
        </form>} />}
    {error && <p role="alert">{error}</p>}
  </div>
}

export function JudgeRecommendationResult({ recommendation }: { recommendation: JudgeBenchRecommendation }) {
  return <RecommendationCard state="idle" question={recommendation.verdict}
    acceptedLabel="" data-run-model={recommendation.model_ref}>
    {recommendation.notes.join(' ') || recommendation.use_case}
  </RecommendationCard>
}

export function ArtifactResult({ artifact, onOpen }: { artifact: Artifact; onOpen: (slug: string) => void }) {
  return <button type="button" aria-label={`Open artifact ${artifact.name}`} onClick={() => onOpen(artifact.slug)}>
    <ArtifactCard title={artifact.name} meta={`${artifact.kind} · version ${artifact.version}`} />
  </button>
}

export function RunHandoffResult({ run }: { run: WorkflowRunDetailData }) {
  const records = Object.entries(run.round_handoff ?? {}).filter(([, row]) => row.completed_role && row.next_role && !row.stop)
  return <div aria-label={`Handoffs for run ${run.run_id}`}>
    {records.map(([id, row]) => <AgentHandoff key={id} from={row.completed_role!} to={row.next_role!}
      carried={row.next_allowed_paths ?? []} settled={false} />)}
  </div>
}
