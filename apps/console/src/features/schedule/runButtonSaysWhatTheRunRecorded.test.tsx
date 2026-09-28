import '@testing-library/jest-dom/vitest'
import { afterEach, expect, it } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import type { ScheduleRun, TriggerReviewCard, TriggerReviewResult } from '../../shared/data/api'
import { RunReceipt } from './ScheduleDetail'
import { runFlashMeta } from './scheduleMeta'
import { announceReviewResult } from '../triggers/TriggerReview'
import { Toaster } from '../../shared/ui/Toaster'

afterEach(cleanup)

it('uses the recorded waiting, launched and queued status with the history sentence', () => {
  for (const [status, label] of [['waiting', 'Waiting for you'], ['launched', 'Launched'], ['queued', 'Queued'], ['success', 'Run finished']]) {
    const record: ScheduleRun = { run_id: 'recorded-run', status, summary: `Recorded ${status} sentence.`, trace: '{"items":1}' }
    const ui = render(<RunReceipt status={record.status} summary={record.summary} />)
    expect(ui.getByRole('status')).toHaveTextContent(`${label} · ${record.summary}`)
    expect(runFlashMeta(record.status).label).toBe(label)
    if (status !== 'success') expect(ui.queryByText('Run finished')).toBeNull()
    expect(ui.queryByText(record.trace!)).toBeNull()
    ui.unmount()
  }
})

it('does not claim completion when no run status was recorded', () => {
  const ui = render(<RunReceipt summary="The action returned no recorded status." />)
  expect(ui.getByRole('status')).toHaveTextContent('Run recorded')
  expect(ui.queryByText('Run finished')).toBeNull()
})

it('announces a restart-review action through the actual toast host using its stored sentence', () => {
  const card: TriggerReviewCard = { id: 'missed:clock:sample', trigger_id: 'store:clock:sample', trigger_name: 'Review documents', reason: 'missed', missed_count: 1, latest_missed_at: 1200, action_revision: 'captured-revision', action: { provider: 'run-workflow', config: { workflow: 'document-review' } } }
  const response: TriggerReviewResult = { ok: true, outcome: 'ran_late', status: 'waiting', summary: 'Waiting for you to approve the document review.', run_id: 'review-record' }
  const ui = render(<Toaster />)
  act(() => announceReviewResult(card, response))
  expect(ui.getByRole('status')).toHaveTextContent(`Review documents: ${response.summary}`)
  expect(ui.queryByText(/ran now|Run finished/)).toBeNull()
})
