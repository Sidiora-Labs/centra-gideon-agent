import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import {
  ApiError,
  type WorkflowDropStatus,
  type WorkflowOutboxEntry,
} from '../../shared/data/api'
import { OutboxPanel } from './OutboxPanel'
import { registerBuiltinContentTypes } from '../../shared/ui/content/registerBuiltins'
import { registerContentType } from '../../shared/ui/content/contentTypes'
import { FileText } from 'lucide-react'

registerBuiltinContentTypes()

registerContentType({
  id: 'probe', label: 'Probe', icon: FileText, tone: 'var(--color-primary)',
  kinds: ['probe'],
  preview: { render: ({ content }: { content: string }) => <pre>{content}</pre> },
  edit: { language: 'plaintext' },
})


const workflowRunOutbox = vi.fn<(id: string) => Promise<{ files: WorkflowOutboxEntry[] }>>()
const workflowRunDropStatus = vi.fn<(id: string) => Promise<WorkflowDropStatus>>()
const artifact = vi.fn()
const artifactVersions = vi.fn<(slug: string) => Promise<{ slug: string; versions: number[] }>>()

vi.mock('@monaco-editor/react', () => ({
  default: () => null,
  DiffEditor: () => null,
}))

vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRunOutbox: (id: string) => workflowRunOutbox(id),
      workflowRunDropStatus: (id: string) => workflowRunDropStatus(id),
      artifact: (slug: string) => artifact(slug),
      artifactVersions: (slug: string) => artifactVersions(slug),
    },
  }
})

function entry(over: Partial<WorkflowOutboxEntry> = {}): WorkflowOutboxEntry {
  return {
    slug: 'report', artifact: 'Weekly report', kind: 'markdown', action: 'version',
    change_note: '18% of the content changed', node_id: 'write',
    updated_at: '2026-08-11T02:00:00+00:00', self_contained: true,
    ...over,
  }
}

function dropOff(): WorkflowDropStatus {
  return {
    enabled: false, reason: 'this workflow does not declare a file drop',
    auto_accept_mimes: [], max_files: 50, files: [],
  }
}

describe('OutboxPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workflowRunDropStatus.mockResolvedValue(dropOff())
  })

  it('lists what the run published, with its change note', async () => {
    workflowRunOutbox.mockResolvedValue({ files: [entry()] })
    render(<OutboxPanel runId="run-1" onClose={() => {}} />)

    expect(await screen.findByText('Weekly report')).toBeTruthy()
    expect(screen.getByText('version')).toBeTruthy()
    expect(workflowRunOutbox).toHaveBeenCalledWith('run-1')
  })

  it('renders a noop republish as "unchanged" rather than hiding it', async () => {
    workflowRunOutbox.mockResolvedValue({ files: [entry({ action: 'noop' })] })
    render(<OutboxPanel runId="run-1" onClose={() => {}} />)

    expect(await screen.findByText('unchanged')).toBeTruthy()
    expect(screen.queryByText('noop')).toBeNull()
  })

  it('flags an artifact that is not self-contained', async () => {
    workflowRunOutbox.mockResolvedValue({ files: [entry({ self_contained: false })] })
    render(<OutboxPanel runId="run-1" onClose={() => {}} />)

    expect(await screen.findByText('not self-contained')).toBeTruthy()
  })

  it('says so plainly when the run published nothing', async () => {
    workflowRunOutbox.mockResolvedValue({ files: [] })
    render(<OutboxPanel runId="run-1" onClose={() => {}} />)

    expect(await screen.findByText(/Nothing published yet/)).toBeTruthy()
  })

  it('offers Rendered, Source and Compare once a multi-version artifact is selected', async () => {
    workflowRunOutbox.mockResolvedValue({ files: [entry()] })
    artifact.mockResolvedValue({ slug: 'report', name: 'Weekly report', kind: 'probe', content: 'Body' })
    artifactVersions.mockResolvedValue({ slug: 'report', versions: [1, 2, 3] })
    render(<OutboxPanel runId="run-1" onClose={() => {}} />)

    const row = await screen.findByRole('button', { name: /Weekly report/ })
    fireEvent.click(row)

    const tabs = await waitFor(() => screen.getByRole('tablist', { name: 'Artifact view' }))
    expect(within(tabs).getByRole('tab', { name: 'Rendered' })).toBeTruthy()
    expect(within(tabs).getByRole('tab', { name: 'Source' })).toBeTruthy()
    expect(within(tabs).getByRole('tab', { name: 'Compare' })).toBeTruthy()
    expect(screen.getByText('18% of the content changed')).toBeTruthy()
    expect(row.getAttribute('aria-pressed')).toBe('true')
  })

  it('withholds Compare when there is only one version', async () => {
    workflowRunOutbox.mockResolvedValue({ files: [entry({ action: 'create' })] })
    artifact.mockResolvedValue({ slug: 'report', name: 'Weekly report', kind: 'probe', content: 'Body' })
    artifactVersions.mockResolvedValue({ slug: 'report', versions: [1] })
    render(<OutboxPanel runId="run-1" onClose={() => {}} />)

    const row = await screen.findByRole('button', { name: /Weekly report/ })
    fireEvent.click(row)

    const tabs = await waitFor(() => screen.getByRole('tablist', { name: 'Artifact view' }))
    expect(within(tabs).getByRole('tab', { name: 'Rendered' })).toBeTruthy()
    expect(within(tabs).queryByRole('tab', { name: 'Compare' })).toBeNull()
  })

  it('explains WHY the file drop is unavailable rather than just hiding it', async () => {
    workflowRunOutbox.mockResolvedValue({ files: [] })
    render(<OutboxPanel runId="run-1" onClose={() => {}} />)

    expect(await screen.findByText(/does not declare a file drop/)).toBeTruthy()
    expect(screen.queryByLabelText(/Choose files/)).toBeNull()
  })

  it('offers a labelled file input when the workflow declared a drop', async () => {
    workflowRunOutbox.mockResolvedValue({ files: [] })
    workflowRunDropStatus.mockResolvedValue({
      enabled: true, reason: '', auto_accept_mimes: ['image/*'], max_files: 50,
      files: [{ filename: 'brief.pdf', size: 2048, sha256: 'abc' }],
    })
    render(<OutboxPanel runId="run-1" onClose={() => {}} />)

    const input = await screen.findByLabelText('Choose files to hand to this run')
    expect(input.getAttribute('type')).toBe('file')
    expect(screen.getByText('brief.pdf')).toBeTruthy()
    expect(screen.getByText('2.0 KB')).toBeTruthy()
  })

  it('renders a calm message when the run is gone', async () => {
    workflowRunOutbox.mockRejectedValue(new ApiError('nope', 404))
    workflowRunDropStatus.mockRejectedValue(new ApiError('nope', 404))
    render(<OutboxPanel runId="run-1" onClose={() => {}} />)

    expect(await screen.findByText(/could not be found/)).toBeTruthy()
  })
})
