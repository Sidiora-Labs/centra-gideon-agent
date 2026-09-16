import { describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const DETAIL = join(process.cwd(), "src/features/workflows/WorkflowDefDetail.tsx")
const META = join(process.cwd(), "src/features/workflows/surfacingMeta.ts")

const HANDOFFS = [
  { target_def: 'bugfix-sop', condition: 'tests failed', context_fields: ['run_id', 'failing_test'], requires_user_request: false },
  { target_def: 'deploy-sop', condition: '', context_fields: [], requires_user_request: true },
]

async function mount(hands_off_to: unknown[] | undefined) {
  vi.resetModules()
  vi.doMock('../../shared/data/api', () => ({
    api: {
      workflowDef: () => Promise.resolve({
        definition: {
          name: 'code-project',
          root: { kind: 'sequence', id: 'root' },
          metadata: hands_off_to === undefined ? {} : { hands_off_to },
        },
        provider: 'bundled',
      }),
      startWorkflowRun: () => Promise.resolve({ run_id: 'r1' }),
      workflowVersions: () => Promise.resolve({
        versions: [], pinned: 0,
        maturity: { level: 0, label: 'draft', signals: {}, clean_runs: 0, evaluator_rejected: false },
      }),
    },
  }))
  const { WorkflowDefDetail } = await import('./WorkflowDefDetail')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<WorkflowDefDetail name="code-project" onBack={() => {}} onStarted={() => {}} />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

describe('declared handoffs reach the def page', () => {
  it('names each target definition', async () => {
    const text = (await mount(HANDOFFS)).container.textContent ?? ''
    expect(text).toContain('Hands off to')
    expect(text).toContain('bugfix-sop')
    expect(text).toContain('deploy-sop')
  })

  it('shows the condition — WHEN the edge is taken', async () => {
    expect((await mount(HANDOFFS)).container.textContent).toContain('tests failed')
  })

  it('shows what carries over to the next workflow', async () => {
    expect((await mount(HANDOFFS)).container.textContent).toContain('carries run_id, failing_test')
  })

  it('marks an edge the system may not take on its own', async () => {
    expect((await mount(HANDOFFS)).container.textContent).toContain('only on request')
  })

  it('does not mark an automatic edge', async () => {
    const { container } = await mount([HANDOFFS[0]])
    expect(container.textContent).not.toContain('only on request')
  })
})

describe('the FE agrees with the engine about which edges exist', () => {
  it('drops an entry with no target_def, exactly as handoffs_from_def does', async () => {
    const { container } = await mount([{ condition: 'orphaned', context_fields: ['x'] }])
    expect(container.textContent).not.toContain('Hands off to')
    expect(container.textContent).not.toContain('orphaned')
  })

  it('drops a blank/whitespace target_def too', async () => {
    const { container } = await mount([{ target_def: '   ', condition: 'blank' }])
    expect(container.textContent).not.toContain('Hands off to')
  })

  it('renders no section when a def declares no handoffs', async () => {
    expect((await mount([])).container.textContent).not.toContain('Hands off to')
  })

  it('survives metadata with no hands_off_to key at all', async () => {
    const { container } = await mount(undefined)
    expect(container.textContent).toContain('Steps')
    expect(container.textContent).not.toContain('Hands off to')
  })
})

describe('the surfacing row keeps its documented distinctions', () => {
  it('needsAttention still reads `overdue`, not a re-derived freshness', () => {
    const meta = readFileSync(META, 'utf8')
    expect(meta).toMatch(/export function needsAttention\(row: Pick<WorkflowSurfacingRow, 'overdue'>/)
    expect(meta).not.toMatch(/row\.last_completed_at/)
  })

  it('the handoff list lives on the def page, not the list row', () => {
    expect(readFileSync(DETAIL, 'utf8')).toMatch(/Hands off to/)
    const list = readFileSync(join(process.cwd(), "src/features/workflows/WorkflowsListPage.tsx"), 'utf8')
    expect(list).not.toMatch(/hands_off_to/)
  })
})
