import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import type { CreatedLoopRun, Loop } from '../../shared/data/api'
import { createdLoopRoute, isCreatedLoopRun } from './creation'

const run: CreatedLoopRun = { run_id: 'run-123', status: 'running', blocking: false, kind: 'general' }
const legacy: Loop = {
  id: 'loop-123', kind: 'goal', name: 'Review', task: 'Review the checklist', execution: 'solo',
  agent: '', model: '', attended: false, max_cycles: 6, idle_secs: 120, success_criteria: null,
  status: 'ready', total_cycles: 0, error_message: null, created_at: 0, started_at: null,
  completed_at: null, kind_config: {},
}

describe('loop creation navigation', () => {
  it('routes a created workflow directly to its run', () => {
    expect(isCreatedLoopRun(run)).toBe(true)
    expect(createdLoopRoute(run)).toBe('workflows/runs/run-123')
    expect(createdLoopRoute({ ...run, run_id: 'a/b' })).toBe('workflows/runs/a%2Fb')
  })
  it('retains goal and code legacy destinations', () => {
    expect(isCreatedLoopRun(legacy)).toBe(false)
    expect(createdLoopRoute(legacy)).toBe('loops/loop-123')
    expect(createdLoopRoute({ ...legacy, kind: 'code' })).toBe('code/loop-123')
  })
  it('narrows both real UI callers before legacy launch actions', () => {
    const section = readFileSync('src/features/loop/LoopSection.tsx', 'utf8')
    const onboarding = readFileSync('src/features/onboarding/tryOneFlows.ts', 'utf8')
    for (const source of [section, onboarding]) {
      expect(source.indexOf('if (isCreatedLoopRun(created))')).toBeLessThan(source.indexOf('api.uLoopAction('))
      expect(source).toContain('createdLoopRoute(created)')
    }
    expect(readFileSync('src/shared/data/api.ts', 'utf8')).toContain("post<Loop | CreatedLoopRun>('/api/loops', body)")
  })
})
