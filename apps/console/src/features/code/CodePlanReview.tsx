import { useEffect, useState } from 'react'
import { fvs } from '../../shared/theme/fontWeight'
import { motion } from 'framer-motion'
import { Code2, Plus, Trash2, X, Rocket, Hand, ChevronLeft, Loader2, ChevronUp, ChevronDown, FileText, Sparkle, FolderOpen, Gauge } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { Button } from '../../shared/ui/Button'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { CapRow, CapabilityPeekModal } from '../../shared/ui/CapabilityPicker'
import { Markdown } from '../../shared/ui/Markdown'
import { spring } from '../../shared/theme/motion'
import { api, SDLC_STAGES, sdlcStageLabel, type Loop, type CodeStage, type PlanStep, type SkillItem, type SkillSearchResult, type WorkflowDefStub } from '../../shared/data/api'
import type { CodeDraft } from './codeDraft'
import { WorkspacePicker } from './WorkspacePicker'
import { PlannerRecoveryNotice } from './CodePlanningView'

const kc = (p: Loop) => (p.kind_config || {}) as Record<string, unknown>

export function CodePlanReview({ draft, onBack, onLaunched }: {
  draft: CodeDraft
  onBack: () => void
  onLaunched: (id: string) => void
}) {
  const [project, setProject] = useState<Loop | null>(null)
  const [stages, setStages] = useState<CodeStage[]>([])
  const [launching, setLaunching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pickWs, setPickWs] = useState(false)
  const [autopilot, setAutopilot] = useState(true)
  const [artifacts, setArtifacts] = useState<PlanStep[]>([])
  const [plannerStopped, setPlannerStopped] = useState(false)
  const [retryingPlanner, setRetryingPlanner] = useState(false)
  const [plannerError, setPlannerError] = useState<string | null>(null)
  const [installedSkills, setInstalledSkills] = useState<SkillItem[]>([])
  const installedWorkflows: WorkflowDefStub[] = []

  const [skillIds, setSkillIds] = useState<Set<string>>(new Set())
  const [workflowIds, setWorkflowIds] = useState<Set<string>>(new Set())
  const [installing, setInstalling] = useState<Record<string, boolean>>({})
  const [installed, setInstalled] = useState<Set<string>>(new Set())
  const marketplaceSuggestions = draft.classification.marketplace_suggestions ?? []

  async function installMarketplaceSkill(s: SkillSearchResult) {
    setInstalling((m) => ({ ...m, [s.id]: true }))
    try {
      const res = await api.installSkill(s.id, s.source || 'skills.sh')
      setInstalled((prev) => new Set(prev).add(s.id))
      const installedKey = res?.path ? res.path.replace(/\/+$/, '').split('/').pop() : undefined
      const fresh = await api.skills().catch(() => installedSkills)
      setInstalledSkills(fresh)
      const match = fresh.find((x) => x.key === installedKey)
        || fresh.find((x) => x.key === s.id || x.name === s.name)
      if (match) setSkillIds((prev) => new Set(prev).add(match.key))
    } catch (e) {
      setError(`Couldn't install “${s.name}”: ${(e as Error).message || 'try again'}`)
    } finally { setInstalling((m) => ({ ...m, [s.id]: false })) }
  }

  useEffect(() => {
    api.uLoop(draft.projectId).then((p) => {
      setProject(p)
      setStages(((p.plan ?? []) as unknown as CodeStage[]).map((s) => ({ ...s, exit_criteria: [...(s.exit_criteria ?? [])] })))
      setAutopilot(p.autopilot !== false)
      setSkillIds(new Set(p.skill_ids ?? []))
      setWorkflowIds(new Set(p.workflow_ids ?? []))
    }).catch(() => setError('Could not load the project.'))
    api.skills().then(setInstalledSkills).catch(() => {})
    api.uLoopPlanState(draft.projectId).then(({ session, planner }) => {
      if (session) setArtifacts(session.steps.filter((st) => st.kind !== 'decomposition' && st.artifact && Object.keys(st.artifact).length > 0))
      setPlannerStopped(planner.retryable)
    }).catch(() => {})
  }, [draft.projectId])

  async function retryPlanning() {
    if (retryingPlanner) return
    setRetryingPlanner(true); setPlannerError(null)
    try {
      await api.uLoopPlanRetry(draft.projectId)
      const state = await api.uLoopPlanState(draft.projectId)
      setPlannerStopped(state.planner.retryable)
    } catch (e) {
      setPlannerError(`Couldn't restart planning: ${(e as Error).message || 'unknown error'}`)
    } finally { setRetryingPlanner(false) }
  }

  const dupStages = (() => {
    const seen = new Set<string>(); const dups = new Set<string>()
    for (const s of stages) {
      const key = ((s.stage || '').trim() || (s.title || '').trim()).toLowerCase()
      if (!key) continue
      if (seen.has(key)) dups.add((s.stage || '').trim() || (s.title || '').trim()); else seen.add(key)
    }
    return [...dups]
  })()

  function patchStage(i: number, patch: Partial<CodeStage>) {
    setStages((prev) => prev.map((s, j) => (j === i ? { ...s, ...patch } : s)))
  }
  function removeStage(i: number) { setStages((prev) => prev.filter((_, j) => j !== i)) }
  function addStage() {
    setStages((prev) => {
      const used = new Set(prev.map((s) => s.stage))
      const lastIdx = prev.reduce((mx, s) => Math.max(mx, SDLC_STAGES.indexOf(s.stage as typeof SDLC_STAGES[number])), -1)
      const forward = SDLC_STAGES.find((sg, i) => i >= lastIdx && !used.has(sg))
      const stage = forward ?? SDLC_STAGES.find((sg) => !used.has(sg)) ?? 'implementation'
      return [...prev, {
        stage, title: 'New stage', objective: '', exit_criteria: [],
        deliverable: '', task_list_name: 'New stage',
      }]
    })
  }
  function move(i: number, dir: -1 | 1) {
    setStages((prev) => {
      const j = i + dir
      if (j < 0 || j >= prev.length) return prev
      const next = [...prev]; const [it] = next.splice(i, 1); next.splice(j, 0, it); return next
    })
  }

  const needsWorkspace = !!project && String(kc(project).project_kind ?? '') === 'brownfield' && !project.workspace_dir

  async function launch() {
    if (launching) return
    if (needsWorkspace) { setPickWs(true); return }
    setLaunching(true); setError(null)
    try {
      const cleaned = stages
        .filter((s) => (s.objective || '').trim() || (s.title || '').trim())
        .map((s) => ({
          ...s,
          task_list_name: (s.task_list_name || s.title || s.stage || '').trim(),
          tasks: (s.tasks ?? []).filter((t) => (t.title || '').trim()),
        }))
      await api.updateULoop(draft.projectId, {
        plan: cleaned, autopilot,
        skill_ids: [...skillIds], workflow_ids: [...workflowIds],
      })
      await api.uLoopAction(draft.projectId, 'start')
      onLaunched(draft.projectId)
    } catch (e) {
      setError((e as Error).message || 'Could not launch the project'); setLaunching(false)
    }
  }

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar
        left={<div className="flex items-center gap-2"><Code2 size={18} className="text-primary" /><span data-type="title-l" className="text-on-surface">Review the plan</span></div>}
        right={<HeaderActions><HeaderControl icon={ChevronLeft} label="Back" onClick={onBack} priority="primary" /></HeaderActions>} />

      <div className="min-h-0 flex-1 overflow-y-auto px-l py-l">
        <div className="mx-auto flex w-full flex-col gap-4" style={{ maxWidth: 'var(--content-width)' }}>
          {!project ? (
            <div className="flex h-40 items-center justify-center"><Loader2 size={22} className="animate-spin text-on-surface-low" /></div>
          ) : (
            <>
              {plannerStopped && <PlannerRecoveryNotice retrying={retryingPlanner} error={plannerError} onRetry={retryPlanning} />}
              { }
              <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={spring.spatialFast}
                className="rounded-xl border border-outline-variant/50 bg-surface-container/60 p-4">
                <p data-type="body-m" className="text-on-surface">{project.task}</p>
                { }
                <div className="mt-2 flex flex-wrap items-center gap-2 text-[0.75rem]">
                  <span className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var">entry: <b className="text-on-surface">{sdlcStageLabel(String(kc(project).entry_stage ?? ''))}</b></span>
                  <span className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var capitalize">{String(kc(project).project_kind ?? '')}</span>
                  {project.workspace_dir && <span className="rounded-pill bg-surface-high px-2 py-0.5 font-mono text-on-surface-var" title={project.workspace_dir}>{project.workspace_dir.split('/').slice(-2).join('/')}</span>}
                  {
}
                  {!!kc(project).verify_command && <span className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var">build: <code className="text-on-surface">{String(kc(project).verify_command)}</code></span>}
                  {!!kc(project).test_command && <span className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var">tests: <code className="text-on-surface">{String(kc(project).test_command)}</code></span>}
                </div>
                {project.success_criteria && (
                  <p data-type="body-s" className="mt-2 text-on-surface-low"><span className="text-on-surface-var">Done when:</span> {project.success_criteria}</p>
                )}
              </motion.div>

              {
}
              {artifacts.length > 0 && <PlanArtifacts steps={artifacts} />}

              {
}
              <PlanCapabilities skills={installedSkills} workflows={installedWorkflows}
                skillIds={skillIds} workflowIds={workflowIds}
                onToggleSkill={(k) => setSkillIds((prev) => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n })}
                onToggleWorkflow={(id) => setWorkflowIds((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })}
                suggested={new Set(project.skill_ids ?? [])} suggestedWf={new Set(project.workflow_ids ?? [])}
                marketplace={marketplaceSuggestions} installing={installing} installed={installed}
                onInstall={installMarketplaceSkill} />

              { }
              <div className="flex items-center justify-between">
                <span data-type="label-s" className="text-on-surface-var" style={fvs(550)}>Stages ahead ({stages.length})</span>
                <button type="button" onClick={addStage} data-type="caption" className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-on-surface-low hover:text-on-surface hover:bg-surface-high"><Plus size={13} /> Add stage</button>
              </div>

              {
}
              { }
              {dupStages.length > 0 && (
                <div role="alert" className="rounded-lg px-3 py-2 text-[0.8125rem]"
                  style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)', color: 'var(--color-warn)' }}>
                  Stages collide on <b>{dupStages.join(', ')}</b> — only the first is kept on launch. Give each a distinct type (or title, if untyped).
                </div>
              )}

              <div className="flex flex-col gap-3">
                {stages.map((s, i) => (
                  <StageCard key={i} index={i} count={stages.length} stage={s}
                    onPatch={(p) => patchStage(i, p)} onRemove={() => removeStage(i)} onMove={(d) => move(i, d)} />
                ))}
                {stages.length === 0 && (
                  <p data-type="body-s" className="rounded-lg border border-dashed border-outline-variant/40 py-8 text-center text-on-surface-low">
                    No stages — add one, or launch to let the worker plan as it goes.
                  </p>
                )}
              </div>

              {error && (
                <div role="alert" data-type="body-s" className="rounded-lg px-4 py-3"
                  style={{ background: 'color-mix(in srgb, var(--color-danger) 8%, transparent)', color: 'var(--color-danger)' }}>{error}</div>
              )}

              { }
              <div className="flex flex-col gap-1.5 rounded-xl border border-outline-variant/50 bg-surface-container/60 p-3">
                <span data-type="label-s" className="text-on-surface-var" style={fvs(550)}>How should it run?</span>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setAutopilot(true)} aria-pressed={autopilot}
                    className={`flex flex-1 items-start gap-2 rounded-lg border p-2.5 text-left transition-colors ${autopilot ? 'border-primary/60 bg-primary/10' : 'border-outline-variant/50 hover:bg-surface-high'}`}>
                    <Rocket size={15} className={`mt-0.5 shrink-0 ${autopilot ? 'text-primary' : 'text-on-surface-low'}`} />
                    <span>
                      <span data-type="body-s" className="block text-on-surface">Autopilot</span>
                      <span data-type="caption" className="block text-on-surface-low">The system queues + drives every phase to completion.</span>
                    </span>
                  </button>
                  <button type="button" onClick={() => setAutopilot(false)} aria-pressed={!autopilot}
                    className={`flex flex-1 items-start gap-2 rounded-lg border p-2.5 text-left transition-colors ${!autopilot ? 'border-primary/60 bg-primary/10' : 'border-outline-variant/50 hover:bg-surface-high'}`}>
                    <Hand size={15} className={`mt-0.5 shrink-0 ${!autopilot ? 'text-primary' : 'text-on-surface-low'}`} />
                    <span>
                      <span data-type="body-s" className="block text-on-surface">One-by-one</span>
                      <span data-type="caption" className="block text-on-surface-low">You queue tasks yourself, at your own pace.</span>
                    </span>
                  </button>
                </div>
              </div>

              <div className="flex items-center justify-end gap-2 pb-4">
                <Button variant="ghost" size="sm" onClick={onBack}>Cancel</Button>
                {
}
                {
}
                <span title={dupStages.length > 0 ? `Resolve the colliding stage${dupStages.length === 1 ? '' : 's'} (${dupStages.join(', ')}) before launching — each needs a distinct type or title.` : needsWorkspace ? 'This brownfield project needs a workspace folder — choosing one starts it.' : undefined}>
                  <Button size="md" onClick={launch} loading={launching} disabled={launching || dupStages.length > 0} disabledReason={dupStages.length > 0 && !launching ? 'Two stages share a name — rename one first' : undefined}>needsWorkspace ? <FolderOpen size={15} /> : <Rocket size={15} /> {launching ? 'Launching…' : needsWorkspace ? 'Choose workspace & launch' : 'Launch'}
                  </Button>
                </span>
              </div>
            </>
          )}
        </div>
      </div>
      {pickWs && (
        <WorkspacePicker mode="brownfield" onClose={() => setPickWs(false)}
          onPick={async (dir) => {
            setPickWs(false); setError(null); setLaunching(true)
            try {
              setProject(await api.updateULoop(draft.projectId, { workspace_dir: dir }))
              await api.uLoopAction(draft.projectId, 'start')
              onLaunched(draft.projectId)
            } catch (e) {
              setError((e as Error).message || 'Could not launch with that folder'); setLaunching(false)
            }
          }} />
      )}
    </div>
  )
}

function PlanCapabilities({ skills, workflows, skillIds, workflowIds, onToggleSkill, suggested, suggestedWf, marketplace, installing, installed, onInstall }: {
  skills: SkillItem[]; workflows: WorkflowDefStub[]
  skillIds: Set<string>; workflowIds: Set<string>
  onToggleSkill: (key: string) => void; onToggleWorkflow: (id: string) => void
  suggested: Set<string>; suggestedWf: Set<string>
  marketplace: SkillSearchResult[]; installing: Record<string, boolean>; installed: Set<string>
  onInstall: (s: SkillSearchResult) => void
}) {
  const [peek, setPeek] = useState<{ kind: 'skill'; skill?: SkillItem } | null>(null)
  const norm = (x: string) => x.toLowerCase().replace(/[^a-z0-9]+/g, '')
  const installedKeys = new Set<string>()
  for (const sk of skills) { installedKeys.add(norm(sk.key)); if (sk.name) installedKeys.add(norm(sk.name)) }
  const marketplaceToShow = marketplace.filter((m) => !(installedKeys.has(norm(m.id)) || installedKeys.has(norm(m.name)) || installed.has(m.id)))
  if (!skills.length && !workflows.length && !marketplaceToShow.length) return null
  const orderedSkills = [...skills].sort((a, b) => Number(suggested.has(b.key)) - Number(suggested.has(a.key)))
  const orderedWorkflows = [...workflows].sort((a, b) => Number(suggestedWf.has(b.id)) - Number(suggestedWf.has(a.id)))
  const selectedCount = skillIds.size + workflowIds.size
  return (
    <div className="rounded-xl border border-outline-variant/50 bg-surface-container/60 p-3.5">
      <div data-type="label-s" className="mb-1 inline-flex items-center gap-1.5 text-on-surface-var" style={fvs(550)}>
        <Sparkle size={14} className="text-primary" /> Capabilities loaded every cycle
      </div>
      <p data-type="caption" className="mb-2 text-on-surface-low">
        The worker loads these actively each cycle. The planner pre-selected what looks relevant — adjust freely.{' '}
        {selectedCount > 0 ? `${selectedCount} selected.` : 'None selected — the agent still trigger-matches skills as it goes.'}
      </p>
      {(!!orderedSkills.length || !!orderedWorkflows.length) && (
        <div className="flex flex-col gap-1.5">
          {orderedSkills.map((s) => (
            <CapRow key={`s-${s.key}`} id={s.key} name={s.name || s.key} description={s.description}
              checked={skillIds.has(s.key)} suggested={suggested.has(s.key)}
              onToggle={() => onToggleSkill(s.key)} onPeek={() => setPeek({ kind: 'skill', skill: s })} icon={<Sparkle size={14} />} />
          ))}
          {
}
        </div>
      )}
      { }
      {marketplaceToShow.length > 0 && (
        <div className="mt-2.5 flex flex-col gap-1.5 border-t border-outline-variant/40 pt-2.5">
          <span data-type="caption" className="text-on-surface-low uppercase tracking-wide">Suggested to install</span>
          {marketplaceToShow.map((m) => (
            <div key={m.id} data-type="body-s" className="flex items-center gap-2 rounded-md bg-surface-high/50 px-2.5 py-1.5">
              <Sparkle size={12} className="shrink-0 text-primary" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-on-surface-var">{m.name}</div>
                {m.description && <div data-type="caption" className="truncate text-on-surface-low">{m.description}</div>}
              </div>
              { }
              <Button variant="tonal" size="xs" className="shrink-0 gap-1 px-2 text-[0.75rem]" loading={!!installing[m.id]} onClick={() => onInstall(m)}>
                <Plus size={11} /> Install
              </Button>
            </div>
          ))}
        </div>
      )}
      {peek && <CapabilityPeekModal peek={peek} onClose={() => setPeek(null)} />}
    </div>
  )
}

function PlanArtifacts({ steps }: { steps: PlanStep[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-xl border border-outline-variant/50 bg-surface-container/60">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        aria-label={open ? 'Hide plan artifacts' : 'Show plan artifacts'}
        data-type="label-s" className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-on-surface-var" style={fvs(550)}>
        <FileText size={14} className="text-primary" />
        Plan artifacts ({steps.length})
        <span data-type="caption" className="text-on-surface-low">{steps.map((s) => s.kind.replace(/_/g, ' ')).join(' · ')}</span>
        {open ? <ChevronUp size={15} className="ml-auto text-on-surface-low" /> : <ChevronDown size={15} className="ml-auto text-on-surface-low" />}
      </button>
      {open && (
        <div className="flex flex-col gap-3 border-t border-outline-variant/40 px-4 py-3">
          {steps.map((s) => {
            const md = typeof s.artifact?.markdown === 'string' ? s.artifact.markdown.trim() : ''
            const structured = (() => {
              if (md || !s.artifact) return ''
              const rest = Object.fromEntries(Object.entries(s.artifact).filter(([k]) => k !== 'markdown'))
              return Object.keys(rest).length ? JSON.stringify(rest, null, 2) : ''
            })()
            return (
              <div key={s.id} className="flex flex-col gap-1">
                <div data-type="body-s" className="flex items-center gap-1.5 text-on-surface">
                  {s.title}
                  <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 text-on-surface-low">{s.kind.replace(/_/g, ' ')}</span>
                </div>
                {md
                  ? <div data-type="body-s" className="text-on-surface-var"><Markdown>{md}</Markdown></div>
                  : structured
                    ? <pre data-type="caption" className="overflow-x-auto rounded-md bg-surface-high/60 p-2 text-on-surface-var whitespace-pre-wrap break-words">{structured}</pre>
                    : <span data-type="caption" className="text-on-surface-low">No detail.</span>}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function StageQualityGate({ stage, onPatch }: { stage: CodeStage; onPatch: (p: Partial<CodeStage>) => void }) {
  const on = typeof stage.metric_pass === 'number'
  const pass = stage.metric_pass ?? 3.5
  const hold = stage.metric_hold ?? 2.0
  function toggle() {
    if (on) onPatch({ metric_pass: undefined, metric_hold: undefined })
    else onPatch({ metric_pass: 3.5, metric_hold: 2.0 })
  }
  function setPass(v: number) {
    const p = Math.max(0, Math.min(5, v))
    onPatch({ metric_pass: p, metric_hold: Math.min(hold, p) })
  }
  function setHold(v: number) {
    onPatch({ metric_hold: Math.max(0, Math.min(pass, v)) })
  }
  return (
    <div className="mt-2 flex flex-col gap-1">
      <button type="button" onClick={toggle} aria-pressed={on}
        data-type="caption" className="flex w-fit items-center gap-1.5 text-on-surface-low uppercase tracking-wide hover:text-on-surface-var">
        <Gauge size={12} className={on ? 'text-primary' : ''} />
        <span>Quality bar</span>
        <span data-type="caption" className={`rounded-pill px-1.5 py-0.5 normal-case tracking-normal ${on ? 'bg-primary-container text-on-primary-container' : 'bg-surface-high text-on-surface-low'}`}>
          {on ? 'on' : 'off'}
        </span>
      </button>
      {on && (
        <div data-type="caption" className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md bg-surface-high/60 px-2.5 py-2 text-on-surface-var">
          <label className="flex items-center gap-1.5">
            <span className="text-on-surface-low">Pass ≥</span>
            <input type="number" min={0} max={5} step={0.5} value={pass}
              onChange={(e) => setPass(parseFloat(e.target.value))} aria-label="Quality pass score"
              className="w-14 rounded bg-surface-high px-1.5 py-0.5 text-on-surface tabular-nums outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
          </label>
          <label className="flex items-center gap-1.5">
            <span className="text-on-surface-low">Hold ≥</span>
            <input type="number" min={0} max={pass} step={0.5} value={hold}
              onChange={(e) => setHold(parseFloat(e.target.value))} aria-label="Quality hold floor"
              className="w-14 rounded bg-surface-high px-1.5 py-0.5 text-on-surface tabular-nums outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
          </label>
          <span data-type="caption" className="text-on-surface-low">score 0–5 · below hold rolls back</span>
        </div>
      )}
    </div>
  )
}

function StageCard({ index, count, stage, onPatch, onRemove, onMove }: {
  index: number; count: number; stage: CodeStage
  onPatch: (p: Partial<CodeStage>) => void; onRemove: () => void; onMove: (dir: -1 | 1) => void
}) {
  const [crit, setCrit] = useState('')
  const [taskTitle, setTaskTitle] = useState('')
  function addCrit() {
    const c = crit.trim()
    if (!c) return
    onPatch({ exit_criteria: [...(stage.exit_criteria ?? []), c] })
    setCrit('')
  }
  function removeCrit(ci: number) {
    onPatch({ exit_criteria: (stage.exit_criteria ?? []).filter((_, j) => j !== ci) })
  }
  function patchTask(ti: number, patch: Partial<{ title: string; description?: string }>) {
    onPatch({ tasks: (stage.tasks ?? []).map((t, j) => (j === ti ? { ...t, ...patch } : t)) })
  }
  function removeTask(ti: number) {
    onPatch({ tasks: (stage.tasks ?? []).filter((_, j) => j !== ti) })
  }
  function addTask() {
    const t = taskTitle.trim()
    if (!t) return
    onPatch({ tasks: [...(stage.tasks ?? []), { title: t, description: '' }] })
    setTaskTitle('')
  }
  return (
    <div className="rounded-xl border border-outline-variant/50 bg-surface-container/60 p-3.5">
      <div className="flex items-start gap-2">
        <div className="mt-1 flex flex-col items-center gap-0.5 text-on-surface-low">
          <SquareIconButton icon={ChevronUp} label="Move stage up" disabled={index === 0} onClick={() => onMove(-1)}
            disabledReason="Already the first stage" />
          <span data-type="caption" className="tabular-nums">{index + 1}</span>
          <SquareIconButton icon={ChevronDown} label="Move stage down" disabled={index === count - 1} onClick={() => onMove(1)}
            disabledReason="Already the last stage" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <input value={stage.title} onChange={(e) => onPatch({ title: e.target.value })} placeholder="Stage title"
              data-type="body-s" className="min-w-0 flex-1 rounded-md bg-surface-high px-2.5 py-1.5 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
            {
}
            <select value={SDLC_STAGES.includes(stage.stage as typeof SDLC_STAGES[number]) ? stage.stage : ''}
              onChange={(e) => onPatch({ stage: e.target.value })} aria-label="Stage type"
              data-type="caption" className="shrink-0 rounded-md bg-surface-high px-2 py-1.5 text-on-surface-var outline-none focus:ring-2 focus:ring-inset focus:ring-primary">
              {!SDLC_STAGES.includes(stage.stage as typeof SDLC_STAGES[number]) && <option value="">— type —</option>}
              {SDLC_STAGES.map((sg) => <option key={sg} value={sg}>{sg}</option>)}
            </select>
            <SquareIconButton icon={Trash2} iconSize={13} tone="danger" label="Remove stage" onClick={onRemove} className="shrink-0" />
          </div>
          <textarea value={stage.objective} onChange={(e) => onPatch({ objective: e.target.value })} rows={2} placeholder="What this stage accomplishes…"
            data-type="body-s" className="mt-2 w-full resize-none rounded-md bg-surface-high px-2.5 py-1.5 text-on-surface-var outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
          { }
          <div className="mt-2 flex flex-col gap-1">
            <span data-type="caption" className="text-on-surface-low uppercase tracking-wide">Done when</span>
            {(stage.exit_criteria ?? []).map((c, ci) => (
              <div key={ci} data-type="body-s" className="flex items-center gap-1.5 rounded-md bg-surface-high/60 px-2 py-1 text-on-surface-var">
                <span className="min-w-0 flex-1">{c}</span>
                <button type="button" onClick={() => removeCrit(ci)} aria-label="Remove criterion" className="shrink-0 text-on-surface-low hover:text-danger"><X size={12} /></button>
              </div>
            ))}
            {
}
            <input value={crit} onChange={(e) => setCrit(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addCrit() } }}
              onBlur={addCrit}
              placeholder="Add a concrete, checkable condition…"
              data-type="body-s" className="rounded-md bg-surface-high px-2.5 py-1.5 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary placeholder:text-on-surface-low" />
          </div>
          {
}
          <StageQualityGate stage={stage} onPatch={onPatch} />
          {
}
          <div className="mt-2 flex flex-col gap-1">
            <span data-type="caption" className="text-on-surface-low uppercase tracking-wide">Tasks{(stage.tasks ?? []).length ? ` (${stage.tasks!.length})` : ''}</span>
            {(stage.tasks ?? []).map((t, ti) => (
              <div key={ti} data-type="body-s" className="flex items-start gap-1.5 rounded-md bg-surface-high/60 px-2 py-1">
                <span className="mt-1.5 size-3 shrink-0 rounded-full border border-outline-variant/60" />
                <div className="flex min-w-0 flex-1 flex-col">
                  <input value={t.title} onChange={(e) => patchTask(ti, { title: e.target.value })}
                    placeholder="Task…"
                    className="min-w-0 flex-1 bg-transparent text-on-surface-var outline-none placeholder:text-on-surface-low focus:ring-2 focus:ring-inset focus:ring-primary" />
                  {
}
                  <input value={t.description ?? ''} onChange={(e) => patchTask(ti, { description: e.target.value })}
                    placeholder="how / details (optional)…"
                    data-type="caption" className="min-w-0 flex-1 bg-transparent text-on-surface-low outline-none placeholder:text-on-surface-low/60 focus:ring-2 focus:ring-inset focus:ring-primary" />
                </div>
                <button type="button" onClick={() => removeTask(ti)} aria-label="Remove task" className="mt-1 shrink-0 text-on-surface-low hover:text-danger"><X size={12} /></button>
              </div>
            ))}
            <div className="flex items-center gap-1.5 rounded-md px-2 py-1">
              <span className="size-3 shrink-0 rounded-full border border-dashed border-outline-variant/50" />
              <input value={taskTitle} onChange={(e) => setTaskTitle(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTask() } }}
                onBlur={addTask}
                placeholder="Add a task…"
                data-type="body-s" className="min-w-0 flex-1 bg-transparent text-on-surface outline-none placeholder:text-on-surface-low focus:ring-2 focus:ring-inset focus:ring-primary" />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
