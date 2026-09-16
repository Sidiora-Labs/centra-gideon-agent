import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ApiError, type WorkflowRunDetailData, type WorkflowWorkspaceReview } from '../../shared/data/api'
import { WorkspacePanel } from './WorkspacePanel'
import { WorkflowRunDetail } from './WorkflowRunDetail'


const workflowRunWorkspace = vi.fn<(id: string) => Promise<WorkflowWorkspaceReview>>()
const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const workflowContinuations = vi.fn<(id: string) => Promise<{ continuations: [] }>>()

vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRunWorkspace: (id: string) => workflowRunWorkspace(id),
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: (id: string) => workflowContinuations(id),
      workflowRunStreamUrl: () => 'http://localhost/stream',
    },
  }
})

function review(over: Partial<WorkflowWorkspaceReview> = {}): WorkflowWorkspaceReview {
  return {
    run_id: 'run-1',
    workspace: {
      run_id: 'run-1',
      path: '/tmp/worktrees/run-1',
      branch: 'gideon/run-run-1',
      alive: true,
      dirty: true,
      changed: [
        { path: 'src/auth.py', status: 'modified', staged: false },
        { path: 'tests/test_auth.py', status: 'added', staged: true },
      ],
      preserved_workspace_path: '/tmp/worktrees/run-1',
    },
    reintegration: {
      run_id: 'run-1',
      branch: 'gideon/run-run-1',
      changed_files: 2,
      conflicts: [],
      verbs: [
        { verb: 'apply_locally', label: 'Apply Locally', detail: 'put the diff in your working tree', safe: true },
        { verb: 'checkout_branch', label: 'Checkout Branch Locally', detail: 'switch to the branch', safe: true },
      ],
      note: 'Nothing is applied automatically.',
    },
    declared: {
      mode: 'worktree',
      isolated: true,
      setup: { ran: ['npm ci'], skipped: [], failed: [], blocked_run: false },
    },
    ...over,
  }
}

describe('WorkspacePanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches on open and renders the diff plus both verbs with their commands', async () => {
    workflowRunWorkspace.mockResolvedValue(review())
    render(<WorkspacePanel runId="run-1" onClose={() => {}} />)

    await waitFor(() => expect(workflowRunWorkspace).toHaveBeenCalledWith('run-1'))

    const files = within(await screen.findByTestId('changed-files')).getAllByRole('listitem')
    expect(files).toHaveLength(2)
    expect(files[0]).toHaveTextContent('src/auth.py')
    expect(files[0]).toHaveTextContent('modified')
    expect(files[1]).toHaveTextContent('staged')

    const verbs = within(screen.getByTestId('reintegration-verbs')).getAllByRole('listitem')
    expect(verbs).toHaveLength(2)
    expect(verbs[0]).toHaveTextContent('git merge --squash gideon/run-run-1')
    expect(verbs[1]).toHaveTextContent('git checkout gideon/run-run-1')
    expect(screen.getByTestId('workspace-mode')).toHaveTextContent('worktree')
    expect(screen.getByTestId('preserved-path')).toHaveTextContent('/tmp/worktrees/run-1')
  })

  it('marks a conflicted apply unsafe, keeps checkout safe, and performs NOTHING', async () => {
    workflowRunWorkspace.mockResolvedValue(review({
      reintegration: {
        run_id: 'run-1', branch: 'gideon/run-run-1', changed_files: 2,
        conflicts: ['src/auth.py'],
        verbs: [
          { verb: 'apply_locally', label: 'Apply Locally', detail: 'put the diff in your tree', safe: false },
          { verb: 'checkout_branch', label: 'Checkout Branch Locally', detail: 'switch to the branch', safe: true },
        ],
        note: '1 file(s) conflict with your working tree — checkout is the safer verb.',
      },
    }))
    render(<WorkspacePanel runId="run-1" onClose={() => {}} />)

    expect(await screen.findByTestId('unsafe-apply_locally')).toBeInTheDocument()
    expect(screen.queryByTestId('unsafe-checkout_branch')).not.toBeInTheDocument()
    expect(within(screen.getByTestId('workspace-conflicts')).getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByTestId('reintegration-note')).toHaveTextContent('checkout is the safer verb')
    const actual = await import('../../shared/data/api')
    expect(Object.keys(actual.api).filter((k) => /reintegrat|applyLocal|checkoutBranch/i.test(k))).toEqual([])
  })

  it('explains a run with no declared workspace instead of rendering an empty panel', async () => {
    workflowRunWorkspace.mockResolvedValue(review({
      workspace: {
        run_id: 'run-1', path: '', branch: '', alive: false, dirty: false,
        changed: [], preserved_workspace_path: '',
      },
      reintegration: { run_id: 'run-1', branch: '', changed_files: 0, conflicts: [], verbs: [], note: '' },
      declared: {},
    }))
    render(<WorkspacePanel runId="run-1" onClose={() => {}} />)

    expect(await screen.findByTestId('workspace-none')).toHaveTextContent(/did not declare a workspace/i)
    expect(screen.queryByTestId('changed-files')).not.toBeInTheDocument()
    expect(screen.queryByTestId('reintegration-verbs')).not.toBeInTheDocument()
  })

  it('surfaces setup failures and a degraded provisioning', async () => {
    workflowRunWorkspace.mockResolvedValue(review({
      declared: {
        mode: 'worktree',
        isolated: false,
        degraded_reason: 'git is not on PATH; using a scratch dir',
        setup: { ran: [], skipped: [], failed: ['npm ci: exited 1: ENOTFOUND registry'], blocked_run: false },
      },
    }))
    render(<WorkspacePanel runId="run-1" onClose={() => {}} />)

    expect(await screen.findByTestId('workspace-degraded')).toHaveTextContent('git is not on PATH')
    expect(within(screen.getByTestId('setup-failed')).getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText(/Setup never blocks a run/i)).toBeInTheDocument()
  })

  it('shows a not-found message on a 404 without crashing the panel', async () => {
    workflowRunWorkspace.mockRejectedValue(new ApiError('gone', 404))
    render(<WorkspacePanel runId="run-1" onClose={() => {}} />)
    expect(await screen.findByText(/could not be found/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Close')).toBeInTheDocument()
  })
})

describe('the run cockpit exposes the Workspace trigger', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workflowContinuations.mockResolvedValue({ continuations: [] })
    workflowRunWorkspace.mockResolvedValue(review())
  })

  it('opens the panel from a TERMINAL run — when the user decides what to do with the work', async () => {
    workflowRun.mockResolvedValue({
      run_id: 'run-1', workflow: 'demo', status: 'complete', spec_version: 1,
      nodes: [{ instance_path: 'root', node_id: 'build', state: 'done' }],
    })
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} />)

    const trigger = await screen.findByTitle(/Workspace — changed files/i)
    expect(workflowRunWorkspace).not.toHaveBeenCalled()

    fireEvent.click(trigger)
    await waitFor(() => expect(workflowRunWorkspace).toHaveBeenCalledWith('run-1'))
    expect(await screen.findByTestId('workspace-panel-body')).toBeInTheDocument()
    expect(await screen.findByTestId('changed-files')).toBeInTheDocument()
  })
})

describe('WorkspacePanel — localhost web preview (EI-8 §6.2)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('offers an Open Preview link to localhost:<port>, named by its port', async () => {
    workflowRunWorkspace.mockResolvedValue(review({
      preview: {
        ports: [
          { port: 5173, url: 'http://localhost:5173', pid: 4242, command: 'node', address: '127.0.0.1' },
          { port: 8080, url: 'http://localhost:8080', pid: 4243, command: 'python3', address: '*' },
        ],
        root: '/tmp/worktrees/run-1',
        scanned: true,
        reason: '',
      },
    }))
    render(<WorkspacePanel runId="run-1" onClose={() => {}} />)

    const rows = within(await screen.findByTestId('preview-ports')).getAllByRole('listitem')
    expect(rows).toHaveLength(2)

    const first = screen.getByRole('link', { name: /Open Preview on port 5173/i })
    expect(first).toHaveAttribute('href', 'http://localhost:5173')
    const second = screen.getByRole('link', { name: /Open Preview on port 8080/i })
    expect(second).toHaveAttribute('href', 'http://localhost:8080')

    for (const link of [first, second]) {
      expect(link).toHaveAttribute('target', '_blank')
      expect(link.getAttribute('rel')).toContain('noopener')
    }
    expect(screen.getByText(/Local only/i)).toBeInTheDocument()
  })

  it('renders the reason when nothing is listening, and offers no link', async () => {
    workflowRunWorkspace.mockResolvedValue(review({
      preview: {
        ports: [],
        root: '/tmp/worktrees/run-1',
        scanned: false,
        reason: 'no port scanner available on this host (install lsof)',
      },
    }))
    render(<WorkspacePanel runId="run-1" onClose={() => {}} />)

    expect(await screen.findByTestId('preview-none')).toHaveTextContent(
      'no port scanner available on this host (install lsof)',
    )
    expect(screen.queryByTestId('preview-ports')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Open Preview/i })).not.toBeInTheDocument()
  })

  it('shows no preview section at all when the backend sent none', async () => {
    workflowRunWorkspace.mockResolvedValue(review())
    render(<WorkspacePanel runId="run-1" onClose={() => {}} />)

    expect(await screen.findByTestId('changed-files')).toBeInTheDocument()
    expect(screen.queryByTestId('preview-ports')).not.toBeInTheDocument()
    expect(screen.queryByTestId('preview-none')).not.toBeInTheDocument()
  })
})
