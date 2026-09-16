import { useEffect, useMemo, useReducer, useRef, type SetStateAction } from 'react'
import { api, type GoalLoop, type GoalType, type GrillPhase, type SkillItem, type SkillSearchResult } from '../../shared/data/api'
import { loopToGoalLoop } from './goalAdapter'
import type { LoopDraft } from './loopDraft'
import { readReviewPhases, reviewQuestions, reviewStages, reviewLaunchPatch, matchInstalledSkill } from './loopReviewState'

function initialReview(draft: LoopDraft) {
  const config = (draft.classification.kind_config ?? {}) as Record<string, unknown>
  const savedPhases = config.grill_phases as GrillPhase[] | undefined
  return {
    loop: null as GoalLoop | null,
    title: draft.classification.title || '', editingTitle: false,
    subGoals: [] as string[], goalType: 'open_ended' as GoalType, verifyCommand: '',
    answers: (config.phase_answers ?? {}) as Record<string, string>, step: 0,
    launching: false, launchError: null as string | null,
    grillPhases: savedPhases?.length ? savedPhases : null,
    grillMemoryHits: 0, grillLoading: false, grillError: null as string | null,
    installedSkills: [] as SkillItem[], agentNames: [] as string[],
    skillIds: new Set(draft.classification.suggested_skill_ids ?? []),
    workflowIds: new Set(draft.classification.suggested_workflow_ids ?? []),
    installing: {} as Record<string, boolean>, installed: new Set<string>(),
    phases: readReviewPhases(config),
  }
}
type Review = ReturnType<typeof initialReview>
type Action = { type: 'patch'; fields: Partial<Review> }
  | { type: 'change'; apply: (state: Review) => Partial<Review> }
  | { type: 'loaded'; loop: GoalLoop }

function reduceReview(state: Review, action: Action): Review {
  const patch = action.type === 'patch' ? action.fields : action.type === 'change' ? action.apply(state) : {
    loop: action.loop, subGoals: action.loop.sub_goals ?? [], goalType: action.loop.goal_type,
    verifyCommand: action.loop.verify_command ?? '', title: state.title || action.loop.name || '',
  }
  return Object.assign({}, state, patch)
}
const message = (error: unknown, fallback: string) => (error as Error)?.message || fallback

export function useLoopReview(draft: LoopDraft, onLaunched: (id: string) => void) {
  const [state, dispatch] = useReducer(reduceReview, draft, initialReview)
  const lifetime = useRef(0)
  const requests = useRef(new Set<string>())
  const patch = (fields: Partial<Review>) => dispatch({ type: 'patch', fields })
  function set<K extends keyof Review>(key: K, value: SetStateAction<Review[K]>) {
    dispatch({ type: 'change', apply: (current) => ({ [key]: typeof value === 'function' ? (value as (prior: Review[K]) => Review[K])(current[key]) : value }) })
  }
  useEffect(() => {
    const generation = ++lifetime.current
    const deliver = (action: Action) => { if (lifetime.current === generation) dispatch(action) }
    void api.uLoop(draft.loopId).then((loop) => deliver({ type: 'loaded', loop: loopToGoalLoop(loop) })).catch(() => {})
    void api.savedAgents().then((agents) => deliver({ type: 'patch', fields: { agentNames: agents.map((agent) => agent.name) } })).catch(() => {})
    void api.skills().then((skills) => deliver({ type: 'patch', fields: { installedSkills: skills } })).catch(() => {})
    return () => { lifetime.current++; requests.current.clear() }
  }, [draft.loopId])

  const questions = useMemo(() => reviewQuestions(state.grillPhases, draft.classification.clarifying_questions), [state.grillPhases, draft.classification.clarifying_questions])
  const stages = reviewStages(state.phases, questions)
  const next = () => set('step', (step) => Math.min(stages.length - 1, step + 1))
  const isCurrent = (generation: number) => lifetime.current === generation

  async function guide() {
    if (requests.current.has('guide')) return
    requests.current.add('guide')
    const generation = lifetime.current
    patch({ grillLoading: true, grillError: null })
    try {
      const result = await api.grillTree(draft.loopId)
      if (!isCurrent(generation)) return
      patch(result.phases?.length
        ? { grillPhases: result.phases, grillMemoryHits: result.memory_hits || 0 }
        : { grillError: 'The planner returned no phases — the flat questions still apply.' })
    } catch (error) {
      if (isCurrent(generation)) patch({ grillError: message(error, 'Could not build the guided decomposition.') })
    } finally {
      if (isCurrent(generation)) { requests.current.delete('guide'); patch({ grillLoading: false }) }
    }
  }

  async function install(suggestion: SkillSearchResult) {
    const operation = `install:${suggestion.id}`
    if (requests.current.has(operation)) return
    requests.current.add(operation)
    const generation = lifetime.current
    set('installing', (flags) => ({ ...flags, [suggestion.id]: true }))
    try {
      const result = await api.installSkill(suggestion.id, suggestion.source || 'skills.sh')
      if (!isCurrent(generation)) return
      set('installed', (ids) => new Set([...ids, suggestion.id]))
      const skills = await api.skills().catch(() => state.installedSkills)
      if (!isCurrent(generation)) return
      const selected = matchInstalledSkill(skills, suggestion, result?.path)
      dispatch({ type: 'change', apply: (current) => ({ installedSkills: skills, skillIds: selected ? new Set([...current.skillIds, selected.key]) : current.skillIds }) })
    } catch { /* The existing selection stays available for retry. */ }
    finally {
      if (isCurrent(generation)) {
        requests.current.delete(operation)
        set('installing', (flags) => ({ ...flags, [suggestion.id]: false }))
      }
    }
  }

  async function launch() {
    if (!state.loop || requests.current.has('launch')) return
    requests.current.add('launch')
    const generation = lifetime.current
    patch({ launching: true, launchError: null })
    try {
      const payload = reviewLaunchPatch(state.loop, { ...state, questions })
      await api.updateULoop(draft.loopId, payload).catch(() => {})
      await api.uLoopAction(draft.loopId, 'start')
      if (isCurrent(generation)) onLaunched(draft.loopId)
    } catch (error) {
      if (isCurrent(generation)) patch({ launching: false, launchError: message(error, 'Could not launch the loop') })
    } finally { if (isCurrent(generation)) requests.current.delete('launch') }
  }

  function toggle(key: 'skillIds' | 'workflowIds', id: string) {
    set(key, (selected) => new Set(selected.has(id) ? [...selected].filter((entry) => entry !== id) : [...selected, id]))
  }
  return { state, set, patch, questions, stages, next, guide, install, launch, toggle }
}
