import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ApiError, type NodeInspect, type WorkflowRunDetailData } from '../../shared/data/api'
import { NodeInspectorDrawer } from './NodeInspectorDrawer'
import { WorkflowRunDetail } from './WorkflowRunDetail'


const workflowRunNodeInspect = vi.fn<(runId: string, nodeId: string) => Promise<NodeInspect>>()
const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const workflowContinuations = vi.fn<(id: string) => Promise<{ continuations: [] }>>()

vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRunNodeInspect: (r: string, n: string) => workflowRunNodeInspect(r, n),
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: (id: string) => workflowContinuations(id),
      workflowRunStreamUrl: () => 'http://localhost/stream',
    },
  }
})

function inspect(over: Partial<NodeInspect> = {}): NodeInspect {
  return {
    run_id: 'run-1', node_id: 'draft', instance_path: 'root', state: 'done',
    resolved_prompt: 'Write the intro section.',
    resolved_inputs: { topic: 'workflows', tone: 'plain' },
    output: 'The intro, in prose.',
    attempts: [{ status: 'ok' }],
    ledger_events: [{ kind: 'step_completed' }, { kind: 'output_written' }],
    cached: true,
    ...over,
  }
}

describe('NodeInspectorDrawer', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches on open and renders all six fields plus the cached badge', async () => {
    workflowRunNodeInspect.mockResolvedValue(inspect())
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)

    await waitFor(() => expect(workflowRunNodeInspect).toHaveBeenCalledWith('run-1', 'draft'))
    expect(await screen.findByTestId('resolved-prompt')).toHaveTextContent('Write the intro section.')
    expect(screen.getByTestId('resolved-inputs')).toHaveTextContent('topic')
    expect(screen.getByTestId('output')).toHaveTextContent('The intro, in prose.')
    expect(within(screen.getByTestId('attempts')).getAllByRole('listitem')).toHaveLength(1)
    expect(within(screen.getByTestId('ledger-events')).getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByTestId('cached-badge')).toHaveTextContent('cached')
  })

  it('renders "fresh" when the output was not cached', async () => {
    workflowRunNodeInspect.mockResolvedValue(inspect({ cached: false }))
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)
    expect(await screen.findByTestId('cached-badge')).toHaveTextContent('fresh')
  })

  it('renders a ref chip for an offloaded prompt and an artifact chip for an offloaded output — no code block, no re-fetch', async () => {
    workflowRunNodeInspect.mockResolvedValue(inspect({
      resolved_prompt: { ref: 'root::prompt' },
      output: { artifact_ref: 'artifact://big-blob' },
    }))
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)

    const chips = await screen.findAllByTestId('ref-chip')
    expect(chips).toHaveLength(2)
    expect(screen.getByText('root::prompt')).toBeInTheDocument()
    expect(screen.getByText('artifact://big-blob')).toBeInTheDocument()
    expect(screen.queryByTestId('resolved-prompt')).not.toBeInTheDocument()
    expect(screen.queryByTestId('output')).not.toBeInTheDocument()
    expect(workflowRunNodeInspect).toHaveBeenCalledTimes(1)
  })

  it('shows a not-terminal message on a 409 without throwing', async () => {
    workflowRunNodeInspect.mockRejectedValue(new ApiError('node not terminal', 409))
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)
    expect(await screen.findByText(/has not finished yet/i)).toBeInTheDocument()
    expect(screen.queryByTestId('resolved-prompt')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Close')).toBeInTheDocument()
  })

  it('shows a not-found message on a 404', async () => {
    workflowRunNodeInspect.mockRejectedValue(new ApiError('gone', 404))
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)
    expect(await screen.findByText(/could not be found/i)).toBeInTheDocument()
  })
})


const TRIAGE_ROWS = [
  {
    kind: 'step_skipped', node_id: 'triage', instance_path: 'root.children[0]', event_id: 'r-evt-1',
    sha: 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678', impact: 'test',
    rationale: 'assertion maintenance only — 3 test file(s), no shipped code',
  },
  {
    kind: 'step_skipped', node_id: 'triage', instance_path: 'root.children[0]', event_id: 'r-evt-2',
    sha: 'bb11223344556677889900aabbccddeeff112233', impact: 'none',
    rationale: 'no runtime surface — 2 doc/CI file(s) only',
  },
  {
    kind: 'step_skipped', node_id: 'triage', instance_path: 'root.children[0]', event_id: 'r-evt-3',
    sha: 'cc99887766554433221100ffeeddccbbaa998877', impact: 'test',
    rationale: 'assertion maintenance only — 1 test file(s), no shipped code',
  },
  { kind: 'step_completed', node_id: 'triage', instance_path: 'root.children[0]', event_id: 'r-evt-4', state: 'done' },
]

describe('the ledger list surfaces a triage skip (SELF-VERIFICATION SC#6)', () => {
  beforeEach(() => vi.clearAllMocks())

  async function openWith(rows: Array<Record<string, unknown>>) {
    workflowRunNodeInspect.mockResolvedValue(inspect({ node_id: 'triage', ledger_events: rows }))
    render(<NodeInspectorDrawer runId="run-1" nodeId="triage" onClose={() => {}} />)
    return within(await screen.findByTestId('ledger-events'))
  }

  it('renders EVERY row under one node id — three skips are three rows, not one', async () => {
    const list = await openWith(TRIAGE_ROWS)
    expect(list.getAllByRole('listitem')).toHaveLength(4)
    expect(screen.getAllByTestId('ledger-event')).toHaveLength(4)
    expect(screen.getByText('Ledger events (4)')).toBeInTheDocument()
    expect(screen.getAllByTestId('ledger-sha')).toHaveLength(3)
    for (const row of TRIAGE_ROWS.slice(0, 3)) {
      expect(screen.getByText(row.sha as string)).toBeInTheDocument()
    }
  })

  it('renders each row\'s sha, impact and rationale — the three fields the skip record carries', async () => {
    await openWith(TRIAGE_ROWS)

    const rationales = screen.getAllByTestId('ledger-rationale').map((n) => n.textContent)
    expect(rationales).toEqual([
      'assertion maintenance only — 3 test file(s), no shipped code',
      'no runtime surface — 2 doc/CI file(s) only',
      'assertion maintenance only — 1 test file(s), no shipped code',
    ])
    expect(screen.getByText(TRIAGE_ROWS[0].sha as string).textContent).toHaveLength(40)
    expect(screen.getAllByTestId('ledger-impact').map((n) => n.textContent)).toEqual(['test', 'none', 'test'])
  })

  it('tells a skip apart from a node that ran — the "no full run spent" half', async () => {
    await openWith(TRIAGE_ROWS)
    expect(screen.getAllByTestId('ledger-kind').map((n) => n.textContent)).toEqual([
      'step_skipped', 'step_skipped', 'step_skipped', 'step_completed',
    ])
    const rows = screen.getAllByTestId('ledger-event')
    expect(within(rows[0]).getByTestId('ledger-rationale')).toBeInTheDocument()
    expect(within(rows[3]).queryByTestId('ledger-rationale')).not.toBeInTheDocument()
  })

  it('renders NO sha/impact/rationale element for rows that carry none', async () => {
    const list = await openWith([{ kind: 'step_completed' }, { kind: 'output_written' }])
    expect(list.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getAllByTestId('ledger-kind').map((n) => n.textContent)).toEqual([
      'step_completed', 'output_written',
    ])
    expect(screen.queryAllByTestId('ledger-sha')).toHaveLength(0)
    expect(screen.queryAllByTestId('ledger-impact')).toHaveLength(0)
    expect(screen.queryAllByTestId('ledger-rationale')).toHaveLength(0)
  })
})

describe('WorkflowRunDetail node rows expose the Inspect affordance', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workflowContinuations.mockResolvedValue({ continuations: [] })
    workflowRunNodeInspect.mockResolvedValue(inspect())
  })

  function runWith(nodeState: string): WorkflowRunDetailData {
    return {
      run_id: 'run-1', workflow: 'demo', status: 'complete', spec_version: 1,
      nodes: [{ instance_path: 'root', node_id: 'draft', state: nodeState }],
    }
  }

  it('offers Inspect on a terminal node and clicking it opens the drawer (fetches that node)', async () => {
    workflowRun.mockResolvedValue(runWith('done'))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} />)

    const trigger = await screen.findByTitle(/Inspect this node/i)
    fireEvent.click(trigger)

    await waitFor(() => expect(workflowRunNodeInspect).toHaveBeenCalledWith('run-1', 'draft'))
    expect(await screen.findByTestId('node-inspector-body')).toBeInTheDocument()
    expect(await screen.findByTestId('resolved-prompt')).toBeInTheDocument()
  })

  it('does NOT offer Inspect on a non-terminal node (the endpoint would 409)', async () => {
    workflowRun.mockResolvedValue(runWith('running'))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} />)

    await waitFor(() => expect(workflowRun).toHaveBeenCalled())
    await screen.findByText('draft')
    expect(screen.queryByTitle(/Inspect this node/i)).not.toBeInTheDocument()
    expect(workflowRunNodeInspect).not.toHaveBeenCalled()
  })
})
