import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const PANEL = join(process.cwd(), "src/features/settings/DoctorPanel.tsx")

const DEFICITS = [
  { key: 'knowledge_missing_embeddings', count: 26, penalty: 13.0, reachable: false },
  { key: 'orphan_locks', count: 26, penalty: 10.0, reachable: true },
  { key: 'skill_aging_due', count: 0, penalty: 0.0, reachable: true },
]

const snapshot = (over: Record<string, unknown> = {}) => ({
  score: 90, target_score: 90, deficits: DEFICITS, plan: [], recent_runs: [], ...over,
})

async function mount(over: Record<string, unknown> = {}) {
  vi.resetModules()
  vi.doMock('../../shared/data/api', () => ({
    api: {
      doctorRemediation: () => Promise.resolve(snapshot(over)),
      doctorRemediationRun: () => Promise.resolve({}),
      doctor: () => Promise.resolve(null),
    },
  }))
  const { RemediationSection } = await import('./DoctorPanel')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<RemediationSection />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

beforeEach(() => { vi.resetModules() })

describe('deficits reach the panel', () => {
  it('lists each measured deficit with its count', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('Knowledge missing embeddings')
    expect(text).toContain('Orphan locks')
    expect(text).toContain('×26')
  })

  it('hides a deficit measured at zero', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text).not.toContain('Skill aging due')
  })

  it('orders reachable deficits first, then by penalty', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text.indexOf('Orphan locks')).toBeLessThan(text.indexOf('Knowledge missing embeddings'))
  })
})

describe('reachable vs unreachable is not flattened', () => {
  it('shows a penalty only for a deficit that actually counts against the score', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('−10.0')
    expect(text).not.toContain('−13.0')
  })

  it('marks an unreachable deficit so Run now is not expected to clear it', async () => {
    expect((await mount()).container.textContent).toContain('not fixable yet')
  })

  it('does not mark a reachable one', async () => {
    const { container } = await mount({
      deficits: [{ key: 'orphan_locks', count: 3, penalty: 6, reachable: true }],
    })
    expect(container.textContent).not.toContain('not fixable yet')
  })
})

describe('the plan explains what Run now would do', () => {
  it('names the jobs when the engine would act', async () => {
    const { container } = await mount({
      score: 60,
      plan: [{ id: 'serving-fs.prune-orphans', status: 'planned', cost: 0 }],
    })
    expect(container.textContent).toContain('Run now would')
    expect(container.textContent).toContain('Serving fs.prune orphans')
  })

  it('says WHY an empty plan is empty when fixable deficits remain', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('already meets its target')
  })

  it('says nothing-to-do when there is genuinely nothing fixable', async () => {
    const { container } = await mount({
      deficits: [{ key: 'knowledge_missing_embeddings', count: 4, penalty: 2, reachable: false }],
    })
    expect(container.textContent).toContain('Nothing to do')
  })
})

describe('the label helper is shared, not duplicated', () => {
  it('capLabel handles snake_case as well as kebab/slash', () => {
    const src = readFileSync(PANEL, 'utf8')
    expect(src).toMatch(/key\.replace\(\/\[-\/_\]\/g, ' '\)/)
    expect((src.match(/function capLabel/g) ?? []).length, 'only one capLabel').toBe(1)
  })
})
