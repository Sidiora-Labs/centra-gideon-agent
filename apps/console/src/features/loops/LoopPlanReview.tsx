import { useReducer, useRef, useState } from 'react'
import { fvs } from '../../shared/theme/fontWeight'
import { motion, AnimatePresence } from 'framer-motion'
import { ArrowLeft, ArrowRight, Play, Plus, X, Sparkles, AlertTriangle, Check, Download, Sparkle, ChevronUp, ChevronDown, Loader2 } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { IconButton } from '../../shared/ui/IconButton'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { CapRow, CapabilityPeekModal } from '../../shared/ui/CapabilityPicker'
import { Button } from '../../shared/ui/Button'
import { Segmented } from '../../shared/ui/Segmented'
import { spring } from '../../shared/theme/motion'
import { api, type GoalLoop, type GoalType, type SkillItem, type SkillSearchResult, type GrillPhase, type WorkflowDefStub } from '../../shared/data/api'
import type { LoopDraft } from './loopDraft'
import { useLoopReview } from './useLoopReview'
import { blankReviewPhase, capabilityChoices, editReviewPhase, suggestedSubGoals, type ReviewPhase as PlanPhase } from './loopReviewState'
import { useRunStream } from './useRunStream'
import { planReviewReducer, emptyPlanReview } from './planReviewFold'
import { QuestionSlider } from './QuestionSlider'
import { PlanStreamReview } from './PlanStreamReview'

const GOAL_TYPES: { id: GoalType; label: string }[] = [
  { id: 'verifiable', label: 'Verifiable' },
  { id: 'open_ended', label: 'Open-ended' },
  { id: 'monitor', label: 'Monitor' },
]

function stopBehavior(loop: GoalLoop, goalType: GoalType): string {
  const limits: Record<string, string> = { quick: 'as soon as a cycle stops adding much', balanced: 'when gains shrink for a couple of cycles', exhaustive: 'only once it’s truly dry' }
  switch (goalType) {
    case 'monitor': return "I'll watch this continuously — you stop it when you're done."
    case 'verifiable': return loop.verify_command ? `I'll keep cycling until \`${loop.verify_command}\` passes, then stop.` : "I'll keep cycling until the success check passes, then stop."
    default: return loop.granularity === 'forever' ? "I'll keep going indefinitely — you stop it when you're satisfied."
      : `I'll stop when new cycles stop adding value — ${limits[loop.granularity] ?? 'when returns drop'} (${loop.granularity}).`
  }
}

export function LoopPlanReview({ draft, onLaunched, onBack }: {
  draft: LoopDraft
  onLaunched: (loopId: string) => void
  onBack: () => void
}) {
  const controller = useLoopReview(draft, onLaunched)
  const { state, set, patch, questions, stages, next, launch } = controller
  const { loop, title, editingTitle, subGoals, goalType, verifyCommand, answers, step,
    launching, launchError, grillPhases, grillMemoryHits, grillLoading, grillError,
    installedSkills, skillIds, workflowIds, installing, installed, phases, agentNames } = state
  const [review, receive] = useReducer((current: ReturnType<typeof emptyPlanReview>, event: { name: string; data?: unknown }) =>
    planReviewReducer(current, event.name, event.data), undefined, emptyPlanReview)
  useRunStream(draft.loopId, true, {
    onSnapshot: () => {},
    onLifecycle: (name, data) => receive({ name, data }),
  })
  const streamingPlan = Boolean(review.buffer.trim())
  const isThorough = (draft.rigor || draft.classification.intake_rigor || '').toLowerCase() === 'thorough'
  const installedWorkflows: WorkflowDefStub[] = []
  const marketplaceSuggestions = draft.classification.marketplace_suggestions ?? []
  const currentStage = stages[step]
  const totalSteps = stages.length
  const hasPlan = stages.includes('plan')
  const hasQuestions = stages.includes('questions')
  const onOverview = currentStage === 'overview'
  const onCapabilities = currentStage === 'capabilities'
  const onQuestions = currentStage === 'questions'
  const onLaunch = currentStage === 'launch'
  const setTitle = (value: string) => set('title', value)
  const setEditingTitle = (value: boolean) => set('editingTitle', value)
  const setGoalType = (value: GoalType) => set('goalType', value)
  const setSubGoals = (value: string[]) => set('subGoals', value)
  const setVerifyCommand = (value: string) => set('verifyCommand', value)
  const setPhases = (value: PlanPhase[]) => set('phases', value)
  const setStep = (value: React.SetStateAction<number>) => set('step', value)

  if (!loop) return <div data-type="body-s" className="flex h-full items-center justify-center text-on-surface-low">Analyzing the plan…</div>

  const header = (
    <TopBar
      left={
        <div className="flex items-center gap-s min-w-0">
          <IconButton icon={ArrowLeft} label="Back" size={40} onClick={onBack} />
          {editingTitle ? (
            <input autoFocus aria-label="Edit the plan title" value={title} onChange={(e) => setTitle(e.target.value)}
              onBlur={() => setEditingTitle(false)} onKeyDown={(e) => { if (e.key === 'Enter') setEditingTitle(false) }}
              data-type="body-m" className="h-8 min-w-[16rem] rounded-md bg-surface-high px-m text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
          ) : (
            <button type="button" onClick={() => setEditingTitle(true)} title="Edit title"
              data-type="title-m" className="truncate text-on-surface hover:text-on-surface-var" style={fvs(500)}>
              {title || 'Untitled loop'}
            </button>
          )}
        </div>
      }
      right={<span data-type="caption" className="text-on-surface-low tabular-nums">Step {step + 1} / {totalSteps}</span>}
    />
  )

  return (
    <div className="flex h-full flex-col bg-surface-low" data-planning-review>
      {header}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-l w-full" style={{ maxWidth: 'var(--content-width)' }}>
          <AnimatePresence mode="wait">
            <motion.div key={step} initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }} transition={spring.spatialFast}>
              {{ overview: (
                <OverviewStep
                  loop={loop} goalType={goalType} setGoalType={setGoalType} rigor={draft.rigor}
                  subGoals={subGoals} setSubGoals={setSubGoals}
                  verifyCommand={verifyCommand} setVerifyCommand={setVerifyCommand}
                  strategyId={loop.strategy_id} multiAgent={draft.classification.execution === 'multi_agent'}
                  unclassified={draft.classification.classified === false}
                  guided={{ phases: grillPhases, memoryHits: grillMemoryHits, loading: grillLoading,
                            error: grillError, isThorough, run: controller.guide,
                            clear: () => patch({ grillPhases: null, grillError: null }) }}
                />
              ), capabilities: (
                <CapabilitiesStep
                  skills={installedSkills} workflows={installedWorkflows}
                  skillIds={skillIds} workflowIds={workflowIds}
                  onToggleSkill={(id) => controller.toggle('skillIds', id)}
                  onToggleWorkflow={(id) => controller.toggle('workflowIds', id)}
                  suggestedSkillIds={draft.classification.suggested_skill_ids ?? []}
                  suggestedWorkflowIds={draft.classification.suggested_workflow_ids ?? []}
                  marketplace={marketplaceSuggestions} installed={installed} installing={installing}
                  onInstall={controller.install}
                />
              ), plan: (
                <PlanStep phases={phases} setPhases={setPhases}
                  skills={installedSkills} workflows={installedWorkflows}
                  agentNames={agentNames} />
              ), questions: (

                <QuestionSlider
                  questions={questions}
                  seed={answers}
                  onExit={() => setStep((s) => s - 1)}
                  submitLabel="Save answers"
                  onSubmit={(record) => { set('answers', (prior) => ({ ...prior, ...record })); next() }}
                />
              ), launch: (
                <LaunchStep loop={loop} title={title} goalType={goalType} subGoals={subGoals}
                  verifyCommand={verifyCommand}
                  skillIds={[...skillIds]} workflowIds={[...workflowIds]}
                  installedSkills={installedSkills} installedWorkflows={installedWorkflows}
                  phases={phases}
                  planReview={streamingPlan ? <PlanStreamReview buffer={review.buffer} complete={review.complete} names={review.names} goal={loop.goal} /> : null}
                  answered={questions.filter((q) => (answers[q.id] ?? '').trim()).length} totalQ={questions.length} />
              ) }[currentStage]}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>

      {review.demotedReason && (
        <div role="status" className="shrink-0 px-l pb-1" style={{ marginInline: 'auto', width: '100%', maxWidth: 'var(--content-width)' }}>
          <div data-type="body-s" className="rounded-lg px-4 py-2.5 flex items-start gap-2" style={{ background: 'color-mix(in srgb, var(--color-warning) 10%, transparent)', color: 'var(--color-warning)' }}>
            <AlertTriangle size={15} className="shrink-0 mt-0.5" />
            <span>Switched to per-stage approval — {review.demotedReason} You’ll be asked to approve each step as it runs.</span>
          </div>
        </div>
      )}

      {review.confirmation && (
        <div role="alert" className="shrink-0 px-l pb-1" style={{ marginInline: 'auto', width: '100%', maxWidth: 'var(--content-width)' }}>
          <div data-type="body-s" className="rounded-lg px-4 py-2.5 flex items-center gap-2" style={{ background: 'color-mix(in srgb, var(--color-info) 10%, transparent)' }}>
            <Check size={15} className="shrink-0 text-info" />
            <span className="flex-1 text-on-surface">{review.confirmation}</span>
            <Button size="sm" variant="secondary" onClick={() => receive({ name: 'confirmation', data: { resolved: true } })}>Confirm</Button>
          </div>
        </div>
      )}

      {launchError && onLaunch && (
        <div role="alert" className="shrink-0 px-l pb-1" style={{ marginInline: 'auto', width: '100%', maxWidth: 'var(--content-width)' }}>
          <div data-type="body-s" className="rounded-lg px-4 py-2.5" style={{ background: 'color-mix(in srgb, var(--color-error) 8%, transparent)', color: 'var(--color-error)' }}>{launchError}</div>
        </div>
      )}

      {!onQuestions && (
        <div className="shrink-0 border-t border-outline-variant/30 px-l py-m flex items-center justify-between" style={{ marginInline: 'auto', width: '100%', maxWidth: 'var(--content-width)' }}>
          <Button variant="ghost" size="sm" onClick={() => step === 0 ? onBack() : setStep((s) => s - 1)}>
            <ArrowLeft size={15} /> {step === 0 ? 'Cancel' : 'Back'}
          </Button>
          {onLaunch ? (

            <Button onClick={launch} loading={launching} loadingLabel="Launching…"><Play size={16} /> Launch</Button>
          ) : (
            <Button size="sm" onClick={() => setStep((s) => s + 1)}>
              {onOverview ? 'Capabilities'
                : onCapabilities && hasPlan ? 'Review plan'
                : hasQuestions ? 'Answer questions'
                : 'Continue'} <ArrowRight size={15} />
            </Button>
          )}
        </div>
      )}
    </div>
  )
}

interface GuidedProps {
  phases: GrillPhase[] | null; memoryHits: number; loading: boolean; error: string | null
  isThorough: boolean; run: () => void; clear: () => void
}

function OverviewStep({ loop, goalType, setGoalType, rigor, subGoals, setSubGoals, verifyCommand, setVerifyCommand, strategyId, multiAgent, unclassified, guided }: {
  loop: GoalLoop; goalType: GoalType; setGoalType: (type: GoalType) => void; rigor: string
  subGoals: string[]; setSubGoals: (value: string[]) => void
  verifyCommand: string; setVerifyCommand: (value: string) => void
  strategyId?: string; multiAgent: boolean; unclassified?: boolean; guided: GuidedProps
}) {
  const options = GOAL_TYPES.map(({ id, label }) => ({ key: id, label }))
  const roster = multiAgent ? loop.roster ?? [] : []
  const verification = verifyCommand.trim() ? 'The supervisor runs this each cycle; exit code 0 means done.' : 'No check yet — without one, the loop runs to its cycle budget instead of self-completing.'
  return <div className="grid gap-l">
    {unclassified && <div role="alert" data-type="body-s" className="flex items-start gap-s rounded-lg border border-warning/30 bg-warning/10 px-m py-m text-warning">
      <AlertTriangle size={15} className="mt-0.5 shrink-0" /><span>I couldn’t analyze this goal automatically, so these are safe defaults (open-ended). Please confirm the goal type and sub-goals below before launching.</span>
    </div>}
    <Section label="Goal and stopping point">
      <div className="flex flex-wrap items-center gap-s"><span data-type="body-s" className="text-on-surface-low">I read this as a</span>
        <Segmented ariaLabel="Goal type" value={goalType} options={options} onChange={(value) => setGoalType(value as GoalType)} />
        <span data-type="body-s" className="text-on-surface-low">goal · {rigor} depth</span>
      </div>
      <p data-type="body-m" className="mt-m text-on-surface-var">{stopBehavior(loop, goalType)}</p>
    </Section>
    <GuidedDecomposition guided={guided} />
    {goalType === 'verifiable' && <Section label="Verify command">
      <input aria-label="Verify command" value={verifyCommand} onChange={(event) => setVerifyCommand(event.currentTarget.value)}
        placeholder="e.g. make ci · npm test · 0 lint warnings" spellCheck={false} data-type="body-s"
        className="h-9 w-full rounded-lg border border-outline-variant/30 bg-surface-low px-m font-mono text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
      <p data-type="caption" className="mt-s text-on-surface-low">{verification}</p>
    </Section>}
    <Section label="Sub-goals (become Tasks)" action={<SuggestMoreSubGoals goal={loop.goal} value={subGoals} onChange={setSubGoals} />}>
      <SubGoalsEdit value={subGoals} onChange={setSubGoals} />
    </Section>
    {roster.length > 0 && <Section label="Execution — multi-agent">
      <p data-type="body-s" className="mb-s text-on-surface-low">Orchestration: <span className="text-on-surface-var">{strategyId ?? 'orchestrator'}</span></p>
      <div className="grid gap-s sm:grid-cols-2">{roster.map((member, index) => <article key={index} className="grid gap-1 rounded-lg bg-surface-container p-m">
        <span data-type="label-s" className="text-on-surface" style={fvs(550)}>{member.role}</span>
        {member.persona && <span data-type="body-s" className="text-on-surface-var">{member.persona}</span>}
        {member.role_hint && <span data-type="caption" className="text-on-surface-low">↳ {member.role_hint}</span>}
      </article>)}</div>
    </Section>}
  </div>
}

function GuidedDecomposition({ guided }: { guided: GuidedProps }) {
  const phases = guided.phases ?? []
  const count = phases.flatMap((phase) => phase.steps).length
  const actions = phases.length ? <div className="flex items-center gap-s">
    <button type="button" onClick={guided.run} data-type="caption" className="text-on-surface-low hover:text-on-surface">Rebuild</button>
    <button type="button" onClick={guided.clear} data-type="caption" className="text-on-surface-low hover:text-on-surface">Use flat questions</button>
  </div> : !guided.loading ? <Button variant="ghost" size="sm" onClick={guided.run}><Sparkles size={14} /> Guide me{guided.isThorough ? ' · recommended' : ''}</Button> : null
  let content: React.ReactNode
  if (guided.loading) content = <div data-type="body-s" className="flex items-center gap-s rounded-lg bg-surface-container p-m text-on-surface-low"><Loader2 size={15} className="animate-spin text-primary" /> Scoping the goal into phases — checking memory for what’s already settled…</div>
  else if (phases.length) content = <div className="grid gap-m">
    {guided.memoryHits > 0 && <p data-type="caption" className="flex items-center gap-1.5 text-primary"><Check size={12} /> Memory-checked — skipped questions you’ve already answered before.</p>}
    <p data-type="body-s" className="text-on-surface-low">{phases.length} phase{phases.length > 1 ? 's' : ''} · {count} scoped question{count > 1 ? 's' : ''} — you’ll walk them next, phase by phase.</p>
    <ol className="grid gap-s">{phases.map((phase, index) => <li key={index} className="border-l-2 border-primary/40 bg-surface-container px-m py-s">
      <div className="flex items-baseline gap-s"><span data-type="caption" className="text-on-surface-low">Phase {index + 1}</span><span data-type="label-s" className="text-on-surface" style={fvs(550)}>{phase.title}</span></div>
      {phase.description && <p data-type="caption" className="mt-1 text-on-surface-low">{phase.description}</p>}
      <p data-type="caption" className="mt-1 text-on-surface-var">{phase.steps.length} question{phase.steps.length > 1 ? 's' : ''}</p>
    </li>)}</ol>
  </div>
  else content = <div className="grid gap-s"><p data-type="body-s" className="text-on-surface-low">Instead of a flat question list, I’ll ask a few <span className="text-on-surface-var">phases</span> of scoped questions that build on each other — memory-checked so I don’t re-ask what I already know. Best for fuzzy goals.</p>
    {guided.error && <p data-type="caption" className="text-warning">{guided.error}</p>}
  </div>
  return <Section label="Guided decomposition" action={actions}>{content}</Section>
}

function CapabilitiesStep({ skills, skillIds, workflowIds, onToggleSkill, suggestedSkillIds, marketplace, installed, installing, onInstall }: {
  skills: SkillItem[]; workflows: WorkflowDefStub[]
  skillIds: Set<string>; workflowIds: Set<string>
  onToggleSkill: (id: string) => void; onToggleWorkflow: (id: string) => void
  suggestedSkillIds: string[]; suggestedWorkflowIds: string[]
  marketplace: SkillSearchResult[]; installed: Set<string>; installing: Record<string, boolean>
  onInstall: (s: SkillSearchResult) => void
}) {

  const { skills: orderedSkills, marketplace: marketplaceToShow } = capabilityChoices(skills, suggestedSkillIds, marketplace, installed)
  const selectedCount = skillIds.size + workflowIds.size
  const [peek, setPeek] = useState<{ kind: 'skill'; skill?: SkillItem } | null>(null)
  return (
    <div className="grid gap-l mx-auto py-m w-full max-w-[760px]">
      <div className="flex flex-col gap-1">
        <h2 data-type="headline-s" className="text-on-surface">Capabilities for this goal</h2>
        <p data-type="body-s" className="text-on-surface-var">
          Pick the skills and workflows the loop should load <span className="text-on-surface">actively every cycle</span>. The planner pre-selected what looks relevant — adjust freely. {selectedCount > 0 ? `${selectedCount} selected.` : 'None selected — the agent will still trigger-match skills as it goes.'}
        </p>
      </div>

      <Section label={`Skills · ${skills.length} installed`}>
        {orderedSkills.length === 0 ? (
          <p data-type="body-s" className="text-on-surface-low">No skills installed.</p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {orderedSkills.map((s) => (
              <CapRow key={s.key} id={s.key} name={s.name} description={s.description}
                checked={skillIds.has(s.key)} suggested={suggestedSkillIds.includes(s.key)}
                onToggle={() => onToggleSkill(s.key)} onPeek={() => setPeek({ kind: 'skill', skill: s })} icon={<Sparkle size={14} />} />
            ))}
          </div>
        )}
      </Section>

      {marketplaceToShow.length > 0 && (
        <Section label="Suggested to install — from the marketplace">
          <div className="flex flex-col gap-1.5">
            {marketplaceToShow.map((s) => {
              const done = installed.has(s.id)
              return (
                <div key={s.id} className="flex items-start gap-s rounded-lg bg-surface-container px-m py-2.5">
                  <span className="shrink-0 mt-0.5 text-on-surface-low"><Sparkle size={14} /></span>
                  <span className="flex-1 min-w-0">
                    <span className="flex items-center gap-1.5">
                      <span data-type="label-s" className="text-on-surface truncate" style={fvs(550)}>{s.name}</span>
                      {typeof s.installs === 'number' && s.installs > 0 && <span data-type="caption" className="shrink-0 text-on-surface-low">{s.installs.toLocaleString()} installs</span>}
                    </span>
                    {s.description && <span data-type="caption" className="block text-on-surface-low line-clamp-2">{s.description}</span>}
                  </span>
                  <button type="button" disabled={done || !!installing[s.id]} onClick={() => onInstall(s)}
                    data-type="caption" className="shrink-0 inline-flex items-center gap-1 rounded-pill border border-outline-variant/50 px-m h-7 text-primary-emphasis hover:bg-surface-high transition-colors disabled:opacity-50">
                    {done ? <><Check size={13} /> Installed</> : installing[s.id] ? 'Installing…' : <><Download size={13} /> Install</>}
                  </button>
                </div>
              )
            })}
          </div>
        </Section>
      )}

      {peek && <CapabilityPeekModal peek={peek} onClose={() => setPeek(null)} />}
    </div>
  )
}

function PhaseCapPicker({ label, options, selected, onChange }: {
  label: string; options: { id: string; name: string }[]; selected: string[]; onChange: (ids: string[]) => void
}) {
  if (!options.length) return null
  const choose = (id: string) => onChange(selected.includes(id) ? selected.filter((value) => value !== id) : selected.concat(id))
  return <div className="grid gap-1"><span data-type="caption" className="text-on-surface-low">{label}</span>
    <div role="group" aria-label={label} className="flex flex-wrap gap-1.5">{options.map(({ id, name }) => <button key={id} type="button"
      title={id} aria-pressed={selected.includes(id)} onClick={() => choose(id)} data-type="caption"
      className={`inline-flex h-7 items-center gap-1 rounded-md border px-2 transition-colors ${selected.includes(id) ? 'border-primary/40 bg-primary/10 text-primary-emphasis' : 'border-outline-variant/30 bg-surface-high text-on-surface-low hover:text-on-surface'}`}>
      {selected.includes(id) && <Check size={10} />}{name}
    </button>)}</div>
  </div>
}

function PhaseCard({ phase, index, total, skills, workflows, agentNames, onChange, onRemove, onMoveUp, onMoveDown }: {
  phase: PlanPhase; index: number; total: number; skills: SkillItem[]; workflows: WorkflowDefStub[]; agentNames: string[]
  onChange: (phase: PlanPhase) => void; onRemove: () => void; onMoveUp: () => void; onMoveDown: () => void
}) {
  function change<K extends keyof PlanPhase>(key: K, value: PlanPhase[K]) { onChange(Object.assign({}, phase, { [key]: value })) }
  const availableAgents = phase.agent_name && !agentNames.includes(phase.agent_name) ? [{ key: phase.agent_name, label: `${phase.agent_name} (not installed)` }] : []
  availableAgents.push(...agentNames.map((key) => ({ key, label: key })))
  const selectors = [
    { label: 'Skills this phase', field: 'skill_ids' as const, options: skills.map((skill) => ({ id: skill.key, name: skill.name })) },
    { label: 'Workflows this phase', field: 'workflow_ids' as const, options: workflows.map((workflow) => ({ id: workflow.id, name: workflow.name })) },
  ]
  return <article className="grid gap-m rounded-xl border border-outline-variant/30 bg-surface p-m">
    <header className="flex flex-wrap items-center gap-s border-b border-outline-variant/20 pb-s">
      <span data-type="caption" className="grid size-7 place-items-center rounded-md bg-primary/10 text-primary-emphasis tabular-nums">{index + 1}</span>
      <input aria-label="Phase role" value={phase.role} onChange={(event) => change('role', event.currentTarget.value)} placeholder="role (e.g. researcher)"
        data-type="label-s" className="h-8 min-w-0 flex-1 rounded-md bg-surface-high px-2 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" style={fvs(550)} />
      {total > 1 && <div className="flex items-center gap-1">
        <SquareIconButton icon={ChevronUp} label="Move phase up" disabled={index === 0} disabledReason="Already the first phase" onClick={onMoveUp} />
        <SquareIconButton icon={ChevronDown} label="Move phase down" disabled={index === total - 1} disabledReason="Already the last phase" onClick={onMoveDown} />
        <SquareIconButton icon={X} iconSize={15} tone="danger" label="Remove phase" onClick={onRemove} />
      </div>}
    </header>
    <div className="flex flex-wrap items-center gap-m">
      <select aria-label="Phase agent" value={phase.agent_name} onChange={(event) => change('agent_name', event.currentTarget.value)} data-type="body-s"
        className="h-8 w-48 rounded-md bg-surface-high px-2 text-on-surface-var outline-none focus:ring-2 focus:ring-inset focus:ring-primary">
        <option value="">default worker</option>{availableAgents.map(({ key, label }) => <option key={key} value={key}>{label}</option>)}
      </select>
      <label data-type="caption" className="inline-flex items-center gap-s text-on-surface-low">min
        <input type="number" min={1} value={phase.min_cycles} onChange={(event) => change('min_cycles', Math.max(1, Number(event.currentTarget.value) || 1))}
          data-type="body-s" className="h-8 w-16 rounded-md bg-surface-high px-2 text-center text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
      </label>
    </div>
    <textarea aria-label="Phase target" value={phase.target} onChange={(event) => change('target', event.currentTarget.value)} rows={2} placeholder="what this phase aims to accomplish"
      data-type="body-s" className="resize-y rounded-lg bg-surface-high px-m py-s text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
    <input aria-label="Phase exit signal" value={phase.phase_exit} onChange={(event) => change('phase_exit', event.currentTarget.value)} placeholder="advance when… (exit signal)"
      data-type="body-s" className="h-8 rounded-md bg-surface-high px-m text-on-surface-var outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
    {selectors.map(({ label, field, options }) => <PhaseCapPicker key={field} label={label} options={options} selected={phase[field]} onChange={(ids) => change(field, ids)} />)}
  </article>
}

function PlanStep({ phases, setPhases, skills, workflows, agentNames }: {
  phases: PlanPhase[]; setPhases: (p: PlanPhase[]) => void
  skills: SkillItem[]; workflows: WorkflowDefStub[]; agentNames: string[]
}) {
  const change = (index: number, operation: PlanPhase | 'remove' | -1 | 1) => setPhases(editReviewPhase(phases, index, operation))
  const add = () => setPhases(phases.concat(blankReviewPhase()))
  return (
    <div className="grid gap-l mx-auto py-m w-full max-w-[760px]">
      <div className="flex flex-col gap-1">
        <h2 data-type="headline-s" className="text-on-surface">Execution plan</h2>
        <p data-type="body-s" className="text-on-surface-var">
          The planner split this goal into phases. Each runs for at least its min cycles, then advances on its exit signal. Capabilities you set here load <span className="text-on-surface">only during that phase</span> — on top of the baseline you picked.
        </p>
      </div>
      <div className="flex flex-col gap-s">
        {phases.map((p, i) => (
          <PhaseCard key={i} phase={p} index={i} total={phases.length}
            skills={skills} workflows={workflows} agentNames={agentNames}
            onChange={(np) => change(i, np)} onRemove={() => change(i, 'remove')}
            onMoveUp={() => change(i, -1)} onMoveDown={() => change(i, 1)} />
        ))}
      </div>
      <button type="button" onClick={add}
        data-type="body-s" className="self-start inline-flex items-center gap-1.5 rounded-pill border border-outline-variant/50 px-m h-8 text-on-surface-var hover:bg-surface-high transition-colors">
        <Plus size={14} /> Add phase
      </button>
    </div>
  )
}

function LaunchStep({ loop, title, goalType, subGoals, verifyCommand, skillIds, workflowIds, installedSkills, installedWorkflows, phases, answered, totalQ, planReview }: {
  loop: GoalLoop; title: string; goalType: GoalType; subGoals: string[]
  verifyCommand: string; skillIds: string[]; workflowIds: string[]
  installedSkills: SkillItem[]; installedWorkflows: WorkflowDefStub[]
  phases: PlanPhase[]; answered: number; totalQ: number
  planReview?: React.ReactNode
}) {
  const typeLabel = new Map(GOAL_TYPES.map((entry) => [entry.id, entry.label])).get(goalType) ?? goalType
  const granularityLabel = loop.granularity.replace(/^./, (letter) => letter.toUpperCase())
  const skillsById = new Map(installedSkills.map((skill) => [skill.key, skill.name]))
  const workflowsById = new Map(installedWorkflows.map((workflow) => [workflow.id, workflow.name]))
  const skillName = (id: string) => skillsById.get(id) ?? id
  const workflowName = (id: string) => workflowsById.get(id) ?? id
  const baselineSkills = skillIds.map(skillName)
  const baselineWorkflows = workflowIds.map(workflowName)
  return (
    <div className="grid gap-l mx-auto py-m w-full max-w-[760px]">
      <h2 data-type="headline-s" className="text-on-surface">Ready to launch</h2>
      <p data-type="body-m" className="text-on-surface-var">{stopBehavior(loop, goalType)}</p>

      {planReview && <Section label="Planned steps">{planReview}</Section>}

      <div data-type="body-s" className="flex flex-col gap-1.5 text-on-surface-low">
        <div>Title: <span className="text-on-surface-var">{title || loop.name}</span></div>
        <div>Type: <span className="text-on-surface-var">{typeLabel}</span> · Mode: <span className="text-on-surface-var">{loop.attended ? 'Attended' : 'Unattended'}</span> · Granularity: <span className="text-on-surface-var">{granularityLabel}</span></div>
        {goalType === 'verifiable' && verifyCommand.trim() && (
          <div>Verify: <code className="text-on-surface-var font-mono">{verifyCommand.trim()}</code></div>
        )}
        {totalQ > 0 && <div>Questions answered: <span className="text-on-surface-var">{answered}/{totalQ}</span> <span className="opacity-70">(the rest I’ll investigate)</span></div>}
      </div>

      <Section label="Goal">
        <p data-type="body-m" className="text-on-surface">{loop.goal}</p>
        {loop.success_criteria && <p data-type="body-s" className="mt-1 text-on-surface-low"><span className="text-on-surface-var">Done when:</span> {loop.success_criteria}</p>}
      </Section>

      {subGoals.length > 0 && (
        <Section label={`Sub-goals · ${subGoals.length} (become Tasks)`}>
          <ul className="flex flex-col gap-1.5">
            {subGoals.map((s, i) => (
              <li key={i} data-type="body-s" className="flex items-start gap-s text-on-surface">
                <span className="mt-2 size-1 shrink-0 rounded-pill bg-primary" />{s}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {phases.length > 0 && (
        <Section label={`Execution plan · ${phases.length} phases`}>
          <div className="flex flex-col gap-1.5">
            {phases.map((p, i) => (
              <div key={i} className="rounded-lg bg-surface-container px-m py-2 flex flex-col gap-0.5">
                <div className="flex items-center gap-s">
                  <span data-type="caption" className="shrink-0 inline-flex size-5 items-center justify-center rounded-pill bg-surface-high text-on-surface-low tabular-nums">{i + 1}</span>
                  <span data-type="label-s" className="text-on-surface" style={fvs(550)}>{p.role || `Phase ${i + 1}`}</span>
                  <span data-type="caption" className="text-on-surface-low">{p.agent_name || 'default worker'} · ≥{Math.max(1, p.min_cycles)} cycle{Math.max(1, p.min_cycles) !== 1 ? 's' : ''}</span>
                </div>
                {p.target && <span data-type="body-s" className="pl-7 text-on-surface-var">{p.target}</span>}
                {(p.skill_ids.length > 0 || p.workflow_ids.length > 0) && (
                  <div data-type="caption" className="pl-7 flex flex-wrap items-center gap-1 mt-0.5">
                    {p.skill_ids.map((s) => <span key={s} className="inline-flex items-center rounded-pill px-1.5 h-5 bg-surface-high text-on-surface-low">{skillName(s)}</span>)}
                    {p.workflow_ids.map((w) => <span key={w} className="inline-flex items-center rounded-pill px-1.5 h-5 bg-surface-high text-on-surface-low">{workflowName(w)}</span>)}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {(baselineSkills.length > 0 || baselineWorkflows.length > 0) && (
        <Section label={phases.length > 0 ? 'Always-on capabilities' : 'Capabilities'}>
          <div data-type="caption" className="flex flex-wrap items-center gap-1">
            {baselineSkills.map((n, i) => <span key={`s${i}`} className="inline-flex items-center rounded-pill px-2 h-5 bg-surface-container text-on-surface-var">{n}</span>)}
            {baselineWorkflows.map((n, i) => <span key={`w${i}`} className="inline-flex items-center rounded-pill px-2 h-5 bg-surface-container text-on-surface-var">{n}</span>)}
          </div>
        </Section>
      )}
    </div>
  )
}

function Section({ label, children, action }: { label: string; children: React.ReactNode; action?: React.ReactNode }) {
  return <section className="grid gap-m rounded-xl border border-outline-variant/30 bg-surface px-m py-m">
    <header className="flex flex-wrap items-center justify-between gap-s border-b border-outline-variant/20 pb-s">
      <h3 data-type="label-s" className="text-on-surface-var">{label}</h3>{action}
    </header>
    <div className="min-w-0">{children}</div>
  </section>
}

function SuggestMoreSubGoals({ goal, value, onChange }: { goal: string; value: string[]; onChange: (v: string[]) => void }) {
  const [busy, setBusy] = useState(false)
  const requesting = useRef(false)
  async function suggest() {
    if (requesting.current || goal.trim().length < 20) return
    requesting.current = true
    setBusy(true)
    try {
      const result = await api.classifyULoop('goal', goal.trim())
      const expanded = suggestedSubGoals(result, value)
      if (expanded.length > value.length) onChange(expanded)
    } catch { /* Suggestion failure leaves the editable list intact. */ }
    finally { requesting.current = false; setBusy(false) }
  }
  const tooShort = goal.trim().length < 20
  return (

    <button type="button" onClick={tooShort ? undefined : suggest}
      disabled={busy}
      aria-disabled={tooShort || undefined}
      title={tooShort ? 'Describe the goal in a bit more detail first' : undefined}
      data-type="body-s" className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-primary-emphasis hover:bg-surface-high transition-colors disabled:opacity-40 aria-disabled:opacity-40">
      {busy ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} Suggest more
    </button>
  )
}

function SubGoalsEdit({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState('')
  function append() {
    const text = draft.trim()
    if (!text) return
    onChange(value.concat(text))
    setDraft('')
  }
  const discard = (index: number) => onChange(value.slice(0, index).concat(value.slice(index + 1)))
  return (
    <div className="flex flex-col gap-1.5">
      {value.map((s, i) => (
        <div key={i} className="flex items-center gap-s rounded-lg bg-surface-container px-m py-2">
          <span className="mt-0.5 size-1 shrink-0 rounded-pill bg-primary" />
          <span data-type="body-s" className="flex-1 min-w-0 text-on-surface">{s}</span>

          <button type="button" aria-label={`Remove sub-goal: ${s.length > 60 ? `${s.slice(0, 60)}…` : s}`}
            onClick={() => discard(i)} className="text-on-surface-low hover:text-on-surface"><X size={14} /></button>
        </div>
      ))}
      <div className="flex items-center gap-s">
        <input value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="New sub-goal"
          onKeyDown={(event) => { if (event.key === 'Enter') append() }}
          placeholder="Add a sub-goal…"
          data-type="body-s" className="flex-1 h-9 rounded-lg bg-surface-container px-m text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
        <IconButton icon={Plus} label="Add sub-goal" size={34} onClick={append} />
      </div>
    </div>
  )
}
