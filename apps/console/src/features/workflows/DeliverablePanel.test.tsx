import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { WorkflowDeliverableAbsence, WorkflowRunDeliverable } from '../../shared/data/api'
import { ABSENT_COPY, DeliverablePanel } from './DeliverablePanel'


let payload: (id: string) => Promise<WorkflowRunDeliverable>

vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: { ...actual.api, workflowRunDeliverable: (id: string) => payload(id) },
  }
})

function present(name: string, content: string): WorkflowRunDeliverable['report'] {
  return {
    name, present: true, content, bytes: content.length, modified_at: 1_788_000_000,
    truncated: false, clipped_blobs: 0, found_in: 'run_dir', absent_reason: null,
  }
}

function absent(
  name: string | null,
  absent_reason: WorkflowDeliverableAbsence,
): WorkflowRunDeliverable['report'] {
  return {
    name, present: false, content: null, bytes: null, modified_at: null,
    truncated: false, clipped_blobs: 0, found_in: null, absent_reason,
  }
}

function body(over: Partial<WorkflowRunDeliverable> = {}): WorkflowRunDeliverable {
  return {
    run_id: 'r1',
    workflow: 'goal-pursuit-open-ended',
    report: absent('REPORT.md', 'not_written'),
    log: absent('FINDINGS.md', 'not_written'),
    derivation: {
      name: 'REPORT.md',
      reason: null,
      declared_by: { kind: 'goal', variant: 'open_ended', name: 'REPORT.md' },
    },
    roots: [{ kind: 'run_dir', path: '/tmp/home/workflows/runs/r1', exists: true }],
    instructed: false,
    ...over,
  }
}

describe('absent renders as absent — not an empty document, not an error', () => {
  it('a run with no document yet says the worker has not written it', async () => {
    payload = () => Promise.resolve(body())
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(ABSENT_COPY.not_written)).toBeTruthy())
    expect(screen.getAllByText(/REPORT\.md/).length).toBeGreaterThan(0)
  })

  it('and adds that nothing ever asked for it, when nothing did', async () => {
    payload = () => Promise.resolve(body())
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(/Waiting will not produce it/)).toBeTruthy())
  })

  it('but does NOT say that when the template does name the document', async () => {
    payload = () => Promise.resolve(body({ instructed: true }))
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(ABSENT_COPY.not_written)).toBeTruthy())
    expect(screen.queryByText(/Waiting will not produce it/)).toBeNull()
  })

  it('a kind that produces no document says so instead of "not written"', async () => {
    payload = () =>
      Promise.resolve(
        body({
          workflow: 'goal-pursuit-verifiable',
          report: absent(null, 'kind_has_no_document'),
          derivation: {
            name: null,
            reason: 'kind_has_no_document',
            declared_by: { kind: 'goal', variant: 'verifiable', name: '' },
          },
        }),
      )
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(ABSENT_COPY.kind_has_no_document)).toBeTruthy())
    expect(screen.queryByText(ABSENT_COPY.not_written)).toBeNull()
  })

  it('renders a distinct sentence for every reason the backend can send', async () => {
    const reasons = Object.keys(ABSENT_COPY) as WorkflowDeliverableAbsence[]
    expect(reasons).toHaveLength(5)
    expect(new Set(Object.values(ABSENT_COPY)).size).toBe(5)
    for (const reason of reasons) {
      payload = () => Promise.resolve(body({ report: absent('REPORT.md', reason) }))
      const view = render(<DeliverablePanel runId="r1" />)
      await waitFor(() => expect(screen.getByText(ABSENT_COPY[reason])).toBeTruthy())
      view.unmount()
    }
  })

  it('names where it looked, so "not written" is checkable', async () => {
    payload = () => Promise.resolve(body())
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(/\/tmp\/home\/workflows\/runs\/r1/)).toBeTruthy())
  })
})

describe('a written document reads as a document', () => {
  it('renders the markdown body and where it came from', async () => {
    payload = () =>
      Promise.resolve(body({ report: present('REPORT.md', '# What I found\n\nIt works.\n') }))
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText('What I found')).toBeTruthy())
    expect(screen.getByText(/read from this run’s run directory/)).toBeTruthy()
    for (const copy of Object.values(ABSENT_COPY)) expect(screen.queryByText(copy)).toBeNull()
  })

  it('the log is a separate slot, reachable from the deliverable', async () => {
    payload = () =>
      Promise.resolve(body({ log: present('FINDINGS.md', 'cycle 1: looked around\n') }))
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(/The working log has content/)).toBeTruthy())
    fireEvent.click(screen.getByRole('tab', { name: /Log · FINDINGS\.md/ }))
    await waitFor(() => expect(screen.getByText(/looked around/)).toBeTruthy())
  })

  it('says so when a blob-shaped run was clipped', async () => {
    payload = () =>
      Promise.resolve(body({ report: { ...present('REPORT.md', 'before after'), clipped_blobs: 2 } }))
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(/2 blobs clipped/)).toBeTruthy())
  })

  it('says so when the body was truncated for display', async () => {
    payload = () =>
      Promise.resolve(
        body({ report: { ...present('REPORT.md', 'long…'), truncated: true, bytes: 900_000 } }),
      )
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(/truncated for display/)).toBeTruthy())
  })
})

describe('the filename is shown with its provenance', () => {
  it('names the kind and variant that declared it', async () => {
    payload = () => Promise.resolve(body())
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(/declared by the/)).toBeTruthy())
    expect(screen.getByText('goal')).toBeTruthy()
    expect(screen.getByText('open_ended')).toBeTruthy()
  })

  it('shows a dash rather than a blank when a slot has no name', async () => {
    payload = () => Promise.resolve(body({ log: absent(null, 'no_root') }))
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /Log · —/ })).toBeTruthy(),
    )
  })
})

describe('a read failure is a failure, not an empty document', () => {
  it('surfaces the error and offers a retry', async () => {
    payload = () => Promise.reject(new Error('gateway said no'))
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText('gateway said no')).toBeTruthy())
    for (const copy of Object.values(ABSENT_COPY)) expect(screen.queryByText(copy)).toBeNull()
  })
})

describe('the panel carries no money and no ROI axis', () => {
  it('renders no cost figure — issue #2566', async () => {
    payload = () => Promise.resolve(body({ report: present('REPORT.md', 'done') }))
    const { container } = render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText('done')).toBeTruthy())
    expect(container.textContent ?? '').not.toMatch(/\$|cost|token|marginal|quality/i)
  })
})
