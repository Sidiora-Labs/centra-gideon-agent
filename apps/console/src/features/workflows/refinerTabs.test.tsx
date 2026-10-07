import { describe, expect, it, vi } from 'vitest'
import { act, render, fireEvent, within, waitFor } from '@testing-library/react'


const MATURITY = { level: 3, label: 'mature', signals: {}, clean_runs: 5, evaluator_rejected: true }
const VERSIONS = [
  { version: 1, source: 'user', created_at: '2026-08-15T00:00:00Z', note: '', run_ids: [], ops_count: 0 },
  { version: 2, source: 'refiner', created_at: '2026-08-15T01:00:00Z', note: '', run_ids: ['r1'], ops_count: 1 },
]

function makeApi(overrides: Record<string, unknown> = {}) {
  return {
    workflowDef: () => Promise.resolve({
      definition: { name: 'code-project', version: 2, source: 'user', description: 'A code project template.', root: { kind: 'sequence', id: 'root' } },
      revision: 'current-definition-revision', provider: 'user',
    }),
    startWorkflowRun: () => Promise.resolve({ run_id: 'r1' }),
    workflowVersion: vi.fn(() => Promise.resolve({ definition: { name: 'code-project', version: 1, source: 'user', description: 'Historical project definition.', root: { kind: 'sequence', id: 'historical-root' } } })),
    saveWorkflowDef: vi.fn((body: { save: boolean }) => Promise.resolve({ valid: true, saved: body.save, name: 'code-project', issues: [] })),
    workflowVersions: () => Promise.resolve({ versions: VERSIONS, pinned: 2, maturity: MATURITY }),
    workflowVersionDiff: () => Promise.resolve({ a: 1, b: 2, ops: [{ op: 'update_node', node_id: 'build', fields: ['retries'] }] }),
    repinWorkflowVersion: vi.fn(() => Promise.resolve({ ok: true, name: 'code-project', pinned: 1 })),
    workflowLedger: () => Promise.resolve({ name: 'code-project', runs: [
      { run_id: 'run-abc', status: 'complete', spec_version: 2, totals: { steps_completed: 3, steps_failed: 0 } },
    ], total: 1 }),
    refineWorkflow: vi.fn(() => Promise.resolve({ run_id: 'refine-run-1' })),
    ...overrides,
  }
}

async function mount(api: Record<string, unknown>, onStarted: (id: string) => void = () => {}) {
  vi.resetModules()
  vi.doMock('../../shared/data/api', () => ({ api }))
  const { WorkflowDefDetail } = await import('./WorkflowDefDetail')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<WorkflowDefDetail name="code-project" onBack={() => {}} onStarted={onStarted} />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

describe('WF2LEA-6 template-detail surfaces', () => {
  it('shows the maturity badge from the versions payload', async () => {
    const text = (await mount(makeApi())).container.textContent ?? ''
    expect(text).toContain('mature')
    expect(text).toContain('L3')
  })

  it('lists version history with a restore preview on the non-pinned version', async () => {
    const r = await mount(makeApi())
    const tab = [...r.container.querySelectorAll('[role="tab"]')].find((b) => (b.textContent ?? '').includes('Versions'))
    await act(async () => { fireEvent.click(tab!); await new Promise((res) => setTimeout(res, 0)) })
    const text = r.container.textContent ?? ''
    expect(text).toContain('v1')
    expect(text).toContain('v2')
    expect(text).toContain('pinned')
    expect(text).toContain('Restore as new version')
    expect(within(r.container).getAllByRole('button', { name: 'Restore as new version' })).toHaveLength(1)
    expect(text).toContain('Latest change')
  })

  it('restores historical content as a new version with current revision authority', async () => {
    const api = makeApi()
    const r = await mount(api)
    const ui = within(r.container)
    fireEvent.click(ui.getByRole('tab', { name: 'Versions' }))
    fireEvent.click(await ui.findByRole('button', { name: 'Restore as new version' }))
    await ui.findByText('Restore v1 as new version')
    expect(api.workflowVersion).toHaveBeenCalledWith('code-project', 1)
    expect(ui.getByLabelText('Description')).toHaveValue('Historical project definition.')
    expect(api.repinWorkflowVersion).not.toHaveBeenCalled()
    expect(api.saveWorkflowDef).not.toHaveBeenCalled()
    fireEvent.click(ui.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.saveWorkflowDef).toHaveBeenCalledTimes(2))
    expect(api.saveWorkflowDef).toHaveBeenNthCalledWith(1, expect.objectContaining({
      name: 'code-project', save: false, based_on: 'code-project', based_on_version: 1,
      root: { kind: 'sequence', id: 'historical-root' },
    }), 'current-definition-revision')
    expect(api.saveWorkflowDef).toHaveBeenNthCalledWith(2, expect.objectContaining({
      name: 'code-project', save: true, create_only: false, expected_revision: 2,
      based_on: 'code-project', based_on_version: 1,
      root: { kind: 'sequence', id: 'historical-root' },
    }), 'current-definition-revision')
    expect(api.repinWorkflowVersion).not.toHaveBeenCalled()
  })

  it('loads the Run Ledger tab lazily', async () => {
    const r = await mount(makeApi())
    const tab = [...r.container.querySelectorAll('[role="tab"]')].find((b) => (b.textContent ?? '').includes('Run Ledger'))
    await act(async () => { fireEvent.click(tab!); await new Promise((res) => setTimeout(res, 0)) })
    const text = r.container.textContent ?? ''
    expect(text).toContain('run-abc')
    expect(text).toContain('3 done')
  })

  it('Refine-now calls the refiner endpoint and navigates to the launched run', async () => {
    const started: string[] = []
    const api = makeApi()
    const r = await mount(api, (id: string) => { started.push(id) })
    const btn = [...r.container.querySelectorAll('button')].find((b) => (b.textContent ?? '').includes('Refine'))
    await act(async () => { fireEvent.click(btn!); await new Promise((res) => setTimeout(res, 0)) })
    expect(api.refineWorkflow as unknown as ReturnType<typeof vi.fn>).toHaveBeenCalledWith('code-project')
    expect(started).toEqual(['refine-run-1'])
  })
})
