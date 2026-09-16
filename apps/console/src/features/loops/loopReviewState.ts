import type { GoalLoop, GoalType, GrillPhase, SkillItem, SkillSearchResult } from '../../shared/data/api'
import type { LoopDraft } from './loopDraft'
import type { SliderQuestion } from './sliderState'

export interface ReviewPhase {
  role: string; agent_name: string; target: string; min_cycles: number
  phase_exit: string; skill_ids: string[]; workflow_ids: string[]
}
export type ReviewStage = 'overview' | 'capabilities' | 'plan' | 'questions' | 'launch'
export const blankReviewPhase = (): ReviewPhase => ({ role: '', agent_name: '', target: '', min_cycles: 1, phase_exit: '', skill_ids: [], workflow_ids: [] })

export function readReviewPhases(config: Record<string, unknown>): ReviewPhase[] {
  const rows = config.execution_plan as Record<string, unknown>[] | undefined
  return (rows ?? []).map((row) => {
    const phase = blankReviewPhase()
    for (const key of ['role', 'agent_name', 'target', 'phase_exit'] as const) phase[key] = String(row[key] ?? '')
    for (const key of ['skill_ids', 'workflow_ids'] as const) phase[key] = Array.isArray(row[key]) ? Array.from(row[key] as unknown[], String) : []
    phase.min_cycles = Number(row.min_cycles ?? 1) || 1
    return phase
  })
}

export function reviewQuestions(phases: GrillPhase[] | null, clarifications: string[] = []): SliderQuestion[] {
  if (!phases?.length) return clarifications.map((prompt, index) => ({ id: `g${index}`, prompt, kind: 'text', required: false }))
  return phases.flatMap((phase, phaseIndex) => phase.steps.map((step, index) => ({
    ...step, id: `p${phaseIndex}s${index}`, prompt: step.prompt, kind: step.kind ?? 'text',
    required: step.required ?? step.kind !== 'boundary',
    phase: phase.title || `Phase ${phaseIndex + 1}`, phaseIndex, phaseCount: phases.length,
  })))
}
export function reviewStages(phases: ReviewPhase[], questions: SliderQuestion[]): ReviewStage[] {
  const stages: ReviewStage[] = ['overview', 'capabilities']
  if (phases.length) stages.push('plan')
  if (questions.length) stages.push('questions')
  stages.push('launch')
  return stages
}

export function reviewLaunchPatch(loop: GoalLoop, fields: {
  title: string; subGoals: string[]; goalType: GoalType; verifyCommand: string
  skillIds: Set<string>; workflowIds: Set<string>; phases: ReviewPhase[]
  grillPhases: GrillPhase[] | null; questions: SliderQuestion[]; answers: Record<string, string>
}) {
  const answered = fields.questions.flatMap((question) => {
    const answer = fields.answers[question.id]?.trim()
    return answer ? [{ question, answer, line: `- ${question.prompt} → ${answer}` }] : []
  })
  let task = loop.goal
  if (answered.length) {
    if (fields.grillPhases?.length) {
      const sections = fields.grillPhases.flatMap((phase, index) => {
        const rows = answered.filter((entry) => entry.question.phaseIndex === index)
        return rows.length ? [`${phase.title || `Phase ${index + 1}`}:\n${rows.map((entry) => entry.line).join('\n')}`] : []
      })
      if (sections.length) task += `\n\nScoping (guided decomposition):\n\n${sections.join('\n\n')}`
    } else task += `\n\nClarifications:\n${answered.map((entry) => entry.line).join('\n')}`
  }
  const kind_config: Record<string, unknown> = { goal_type: fields.goalType, sub_goals: fields.subGoals }
  if (fields.phases.length) kind_config.execution_plan = fields.phases
  if (fields.goalType === 'verifiable') kind_config.verify_command = fields.verifyCommand.trim()
  if (fields.grillPhases?.length) {
    kind_config.grill_phases = fields.grillPhases
    kind_config.phase_answers = Object.fromEntries(answered.map(({ question, answer }) => [question.id, answer]))
  }
  return { name: fields.title.trim() || loop.name, task, plan: fields.subGoals.map((title) => ({ title })), skill_ids: [...fields.skillIds], workflow_ids: [...fields.workflowIds], kind_config }
}

export function capabilityChoices(skills: SkillItem[], suggestions: string[], marketplace: SkillSearchResult[], installed: Set<string>) {
  const normalize = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, '')
  const known = new Set(skills.flatMap((skill) => [skill.key, skill.name].filter(Boolean).map(normalize)))
  const priority = new Set(suggestions)
  return {
    skills: [...skills.filter((skill) => priority.has(skill.key)), ...skills.filter((skill) => !priority.has(skill.key))],
    marketplace: marketplace.filter((skill) => !installed.has(skill.id) && ![skill.id, skill.name].some((value) => known.has(normalize(value)))),
  }
}
export function matchInstalledSkill(skills: SkillItem[], suggestion: SkillSearchResult, path?: string): SkillItem | undefined {
  const segments = path?.replace(/\/+$/, '').split('/')
  const key = segments?.[segments.length - 1]
  return skills.find((skill) => skill.key === key) ?? skills.find((skill) => skill.key === suggestion.id || skill.name === suggestion.name)
}
export function suggestedSubGoals(result: LoopDraft['classification'], current: string[]): string[] {
  const existing = new Set(current.map((title) => title.trim().toLowerCase()))
  const config = result.kind_config as Record<string, unknown> | undefined
  const candidates = config?.sub_goals as string[] | undefined ?? result.plan?.map((row) => String(row.title ?? '')).filter(Boolean) ?? []
  return current.concat(candidates.filter((title) => title.trim() && !existing.has(title.trim().toLowerCase())))
}
export function editReviewPhase(phases: ReviewPhase[], index: number, change: ReviewPhase | 'remove' | -1 | 1): ReviewPhase[] {
  if (change === 'remove') return phases.filter((_, slot) => slot !== index)
  const updated = phases.slice()
  if (typeof change === 'number') {
    const destination = index + change
    if (destination < 0 || destination >= phases.length) return phases
    updated.splice(index, 1)
    updated.splice(destination, 0, phases[index])
  } else updated[index] = change
  return updated
}
