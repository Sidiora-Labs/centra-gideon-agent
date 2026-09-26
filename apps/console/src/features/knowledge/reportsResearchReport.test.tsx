import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ResearchReport as SavedReport } from '../../shared/data/api'
import { api } from '../../shared/data/api'
import { ResearchReport as ResearchReportCard, type ReportSection } from '../../shared/vendor/assistant-ui/elements/research-report'
import { ReportRow } from './ReportsPage'

const saved: SavedReport = {
  id: 'report-queue-7',
  name: 'Queue recovery review',
  prompt: 'Compare the restart timeline with the queue replay.',
  schedule: { kind: 'cron', cron_expr: '0 8 * * 1' },
  tz: 'UTC',
  source: { tags: ['incident', 'queue'], window_secs: 0 },
  context: null,
  citation_policy: 'cite-source-only',
  iteration_cap: 3,
  enabled: true,
  created_ts: 1_787_000_000,
  last_run_ts: null,
  last_status: '',
  last_error: '',
  watermark_ts: 0,
}

const sections: ReportSection[] = [
  { id: 'summary', heading: 'Summary', state: 'done', sources: 2, preview: 'Queue drained.' },
  { id: 'timeline', heading: 'Timeline', state: 'writing', sources: 1 },
  { id: 'follow-up', heading: 'Follow-up', state: 'pending', sources: 0 },
]

afterEach(() => vi.restoreAllMocks())

describe('ResearchReport receives only producer-supplied progress', () => {
  it('renders a named report and its real prompt/status/time without a fabricated section or source count', () => {
    const { container } = render(<ResearchReportCard title="Queue recovery review"
      prompt="Compare the restart timeline with the queue replay."
      details={['cron 0 8 * * 1', 'tagged incident, queue']}
      lastStatus="status completed" lastRun="ran two days ago" />)
    const card = within(container.querySelector('[data-slot="research-report"]') as HTMLElement)
    expect(card.getByText('Queue recovery review')).toBeInTheDocument()
    expect(card.getByText('Compare the restart timeline with the queue replay.')).toBeInTheDocument()
    expect(card.getByText('cron 0 8 * * 1 · tagged incident, queue')).toBeInTheDocument()
    expect(card.getByText('status completed')).toBeInTheDocument()
    expect(card.getByText('ran two days ago')).toBeInTheDocument()
    expect(card.queryByText(/sections|sources read/)).toBeNull()
    expect(card.queryByText('Summary')).toBeNull()
  })

  it('shows explicit empty progress but hides missing progress and nonfinite source counts', () => {
    const { rerender } = render(<ResearchReportCard title="New investigation" sections={[]} sourcesRead={0} />)
    expect(screen.getByText('0/0 sections · 0 sources read')).toBeInTheDocument()
    rerender(<ResearchReportCard title="New investigation" sections={[]} />)
    expect(screen.getByText('0/0 sections')).toBeInTheDocument()
    expect(screen.queryByText(/sources read/)).toBeNull()
    rerender(<ResearchReportCard title="New investigation" sourcesRead={4} />)
    expect(screen.getByText('4 sources read')).toBeInTheDocument()
    expect(screen.queryByText(/sections/)).toBeNull()
    rerender(<ResearchReportCard title="New investigation" sourcesRead={Number.NaN} />)
    expect(screen.queryByText(/sources read|sections/)).toBeNull()
  })

  it('updates completed sections from actual states and retains supplied previews and counts', () => {
    const { rerender } = render(<ResearchReportCard title="Queue recovery review" sections={sections} sourcesRead={3} />)
    expect(screen.getByText('1/3 sections · 3 sources read')).toBeInTheDocument()
    expect(screen.getByText('Queue drained.')).toBeInTheDocument()
    expect(screen.getByText('2 src')).toBeInTheDocument()
    expect(screen.getByText('1 src')).toBeInTheDocument()
    expect(screen.queryByText('0 src')).toBeNull()
    rerender(<ResearchReportCard title="Queue recovery review"
      sections={sections.map(section => ({ ...section, state: 'done' as const }))} sourcesRead={4} />)
    expect(screen.getByText('3/3 sections · 4 sources read')).toBeInTheDocument()
  })

  it('shows a supplied failure as an alert, without inventing a failed run for ordinary status', () => {
    const { rerender } = render(<ResearchReportCard title="Queue recovery review"
      lastStatus="last run failed" statusError lastError="model timeout" />)
    expect(screen.getByRole('alert')).toHaveTextContent('last run failed · model timeout')
    rerender(<ResearchReportCard title="Queue recovery review" lastStatus="status completed" />)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByText('status completed')).toBeInTheDocument()
  })
})

describe('scheduled report rows use the donor without losing their actions', () => {
  it('shows each source-derived detail once and keeps absent progress absent', () => {
    const { container } = render(<ReportRow report={saved} onChanged={() => {}} />)
    const donor = within(container.querySelector('[data-slot="research-report"]') as HTMLElement)
    expect(donor.getAllByText(saved.name)).toHaveLength(1)
    expect(donor.getByText(saved.prompt)).toBeInTheDocument()
    expect(donor.getByText(/cron 0 8 \* \* 1/)).toBeInTheDocument()
    expect(donor.getByText(/tagged incident, queue/)).toBeInTheDocument()
    expect(donor.getByText(/cites new material only/)).toBeInTheDocument()
    expect(donor.getByText('never run')).toBeInTheDocument()
    expect(donor.queryByText(/sections|sources read/)).toBeNull()
    expect(screen.getByRole('switch', { name: `${saved.name} enabled` })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: `Run ${saved.name} now` })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: `Delete ${saved.name}` })).toBeInTheDocument()
  })

  it('shows latest persisted error and run time without adding phantom report sections', () => {
    const report = { ...saved, last_run_ts: 1_787_100_000, last_status: 'error', last_error: 'model timeout',
      citation_policy: 'allow-citing-context' as const }
    const { container } = render(<ReportRow report={report} onChanged={() => {}} />)
    const donor = within(container.querySelector('[data-slot="research-report"]') as HTMLElement)
    expect(donor.getByRole('alert')).toHaveTextContent('last run failed · model timeout')
    expect(donor.getByText(/ran /)).toBeInTheDocument()
    expect(donor.getByText(/may cite context/)).toBeInTheDocument()
    expect(donor.queryByText(/sections|sources read/)).toBeNull()
  })

  it('keeps toggle, run, and delete bound to the persisted report ID', async () => {
    const update = vi.spyOn(api, 'updateResearchReport').mockResolvedValue(saved)
    const run = vi.spyOn(api, 'runResearchReport').mockResolvedValue({ ok: true, report_id: saved.id })
    const remove = vi.spyOn(api, 'deleteResearchReport').mockResolvedValue(undefined)
    const onChanged = vi.fn()
    render(<ReportRow report={saved} onChanged={onChanged} />)
    fireEvent.click(screen.getByRole('switch', { name: `${saved.name} enabled` }))
    await waitFor(() => expect(update).toHaveBeenCalledWith(saved.id, { enabled: false }))
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: `Run ${saved.name} now` }))
    await waitFor(() => expect(run).toHaveBeenCalledWith(saved.id))
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(2))
    fireEvent.click(screen.getByRole('button', { name: `Delete ${saved.name}` }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith(saved.id))
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(3))
  })
})
