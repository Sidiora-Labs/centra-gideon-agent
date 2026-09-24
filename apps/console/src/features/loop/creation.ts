import type { CreatedLoopRun, Loop } from '../../shared/data/api'

export function isCreatedLoopRun(created: Loop | CreatedLoopRun): created is CreatedLoopRun {
  return 'run_id' in created
}

export function createdLoopRoute(created: Loop | CreatedLoopRun): string {
  if (isCreatedLoopRun(created)) return `workflows/runs/${encodeURIComponent(created.run_id)}`
  return `${created.kind === 'code' ? 'code' : 'loops'}/${encodeURIComponent(created.id)}`
}
