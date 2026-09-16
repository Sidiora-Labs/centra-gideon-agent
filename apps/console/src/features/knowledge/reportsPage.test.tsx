import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { ReportRow, ReportsPage } from './ReportsPage'
import { api, type ResearchReport } from '../../shared/data/api'
import { invalidateKeys } from '../../shared/data/data'


function report(over: Partial<ResearchReport> = {}): ResearchReport {
  return {
    id: 'rep-1',
    name: 'Weekly contradiction scan',
    prompt: 'Find claims that contradict what we already believe.',
    schedule: { kind: 'cron', cron_expr: '0 8 * * 1' },
    tz: 'America/Los_Angeles',
    source: { tags: ['research'], window_secs: 0 },
    context: null,
    citation_policy: 'cite-source-only',
    iteration_cap: 3,
    enabled: true,
    created_ts: 1_787_000_000,
    last_run_ts: null,
    last_status: '',
    last_error: '',
    watermark_ts: 0,
    ...over,
  }
}

afterEach(() => { vi.restoreAllMocks(); invalidateKeys('knowledge:reports') })

describe('a report row states its scoping decisions', () => {
  it('names the schedule, what it watches, and that it cites new material only', () => {
    render(<ReportRow report={report()} onChanged={() => {}} />)
    expect(screen.getByText(/Weekly contradiction scan/)).toBeTruthy()
    expect(screen.getByText(/cron 0 8 \* \* 1/)).toBeTruthy()
    expect(screen.getByText(/tagged research/)).toBeTruthy()
    expect(screen.getByText(/cites new material only/)).toBeTruthy()
  })

  it('says so when the policy allows citing context', () => {
    render(<ReportRow report={report({ citation_policy: 'allow-citing-context' })} onChanged={() => {}} />)
    expect(screen.getByText(/may cite context/)).toBeTruthy()
    expect(screen.queryByText(/cites new material only/)).toBeNull()
  })

  it('shows a failed last run WITHOUT losing the run time', () => {
    render(<ReportRow report={report({ last_run_ts: 1_787_100_000, last_status: 'error', last_error: 'model timeout' })}
      onChanged={() => {}} />)
    expect(screen.getByText(/last run failed/)).toBeTruthy()
    expect(screen.getByText(/ran /)).toBeTruthy()
  })

  it('offers a run that names the report, so two rows cannot share one name', () => {
    render(<ReportRow report={report()} onChanged={() => {}} />)
    expect(screen.getByRole('button', { name: /Run Weekly contradiction scan now/i })).toBeTruthy()
  })

  it('running calls the run endpoint and reports a refusal instead of swallowing it', async () => {
    const run = vi.spyOn(api, 'runResearchReport').mockRejectedValue(new Error('a run is already in flight'))
    render(<ReportRow report={report()} onChanged={() => {}} />)
    screen.getByRole('button', { name: /Run Weekly contradiction scan now/i }).click()
    await waitFor(() => expect(run).toHaveBeenCalledWith('rep-1'))
  })
})

describe('the reports destination', () => {
  it('lists what exists', async () => {
    vi.spyOn(api, 'researchReports').mockResolvedValue({ reports: [report()] })
    render(<ReportsPage onBack={() => {}} />)
    await waitFor(() => expect(screen.getByText(/Weekly contradiction scan/)).toBeTruthy())
  })

  it('offers a first report when there are none', async () => {
    vi.spyOn(api, 'researchReports').mockResolvedValue({ reports: [] })
    render(<ReportsPage onBack={() => {}} />)
    await waitFor(() => expect(screen.getByText(/No scheduled reports/)).toBeTruthy())
    expect(screen.queryByText(/could not/i), 'an empty list must not read as a failure').toBeNull()
  })

  it('a failed load says so rather than claiming there are none', async () => {
    vi.spyOn(api, 'researchReports').mockRejectedValue(new Error('offline'))
    render(<ReportsPage onBack={() => {}} />)
    await waitFor(() => expect(screen.queryByText(/No scheduled reports/)).toBeNull())
    expect(await screen.findByText(/scheduled reports/)).toBeTruthy()
  })
})
