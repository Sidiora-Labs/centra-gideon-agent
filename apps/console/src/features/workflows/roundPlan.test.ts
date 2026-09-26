import { describe, expect, it } from 'vitest'
import type { WorkflowDef } from '../../shared/data/api'
import { roundPlan, roundPlanText, roundPlanWithInputs } from './roundPlan'

describe('reviewed repository rounds', () => {
  it('shows the branch, role boundaries, real checks and stop condition before launch', () => {
    const definition = {
      name: 'Reviewed work',
      root: { kind: 'sequence', id: 'root', children: [{ kind: 'loop', id: 'rounds', config: {
        round_protocol: {
          branch: 'reviewed-work', max_rounds: 3, stop_marker: 'DONE',
          roles: [
            { name: 'planner', allowed_paths: ['plan.md'] },
            { name: 'builder', allowed_paths: ['src/**'] },
            { name: 'verifier', allowed_paths: ['checks/**'] },
          ],
        },
      }, body: { kind: 'transform', id: 'work' } }] },
    } as WorkflowDef
    const plan = roundPlan(definition)
    expect(plan).not.toBeNull()
    const review = roundPlanText(roundPlanWithInputs(plan!, {
      worktree: '/project/worktrees/reviewed-work', verify_command: 'pytest checks/',
    }))
    expect(review).toContain('Branch: reviewed-work')
    expect(review).toContain('planner (plan.md) → builder (src/**) → verifier (checks/**)')
    expect(review).toContain('Verification: pytest checks/')
    expect(review).toContain('Stop marker: DONE')
  })
})
