import type { GoalLoop, GoalType, Granularity, Loop } from '../../shared/data/api'

export function loopToGoalLoop(loop: Loop): GoalLoop {
  const config = (loop.kind_config || {}) as Record<string, unknown>
  const lists = Object.fromEntries(['sub_goals', 'deliverables', 'scope', 'rubric'].map((key) => [
    key, Array.isArray(config[key]) ? Array.from(config[key] as unknown[], String) : [],
  ])) as Pick<GoalLoop, 'sub_goals' | 'deliverables' | 'scope' | 'rubric'>
  const result = Object.assign({}, loop, lists, {
    goal: loop.task,
    goal_type: (config.goal_type ?? 'open_ended') as GoalType,
    granularity: (config.granularity ?? 'balanced') as Granularity,
    verify_command: String(config.verify_command ?? ''),
    ratchet_mode: config.ratchet_mode ? String(config.ratchet_mode) : undefined,
    execution_plan: Array.isArray(config.execution_plan) ? config.execution_plan as Record<string, unknown>[] : [],
  })
  return result as unknown as GoalLoop
}
