import '@testing-library/jest-dom/vitest'
import { beforeEach, expect, it } from 'vitest'
import { fireEvent, render, waitFor } from '@testing-library/react'
import { resetDataStore, writeQuery } from '../../shared/data/data'
import type { TriggerReviewCard } from '../../shared/data/api'
import { ReviewCard, TriggerReview, reviewDecision } from './TriggerReview'

const missed: TriggerReviewCard = {
  id: 'missed-summary', trigger_id: 'store:daily-summary', trigger_name: 'Daily summary', reason: 'missed',
  missed_count: 3, latest_missed_at: Date.now() / 1000 - 300, action_revision: 'captured-summary-revision',
  action: { provider: 'run-workflow', config: { workflow: 'Summary' } },
}
beforeEach(() => resetDataStore())
it('shows actual cached missed and interrupted cards with their frozen action and captured decision revision', () => {
  const interrupted: TriggerReviewCard = { ...missed, id: 'interrupted-summary', trigger_id: 'store:other-summary', trigger_name: 'Interrupted summary', reason: 'interrupted', run_id: 'interrupted-run' }
  writeQuery('triggers:review', [missed, interrupted])
  let opened = ''
  const ui = render(<TriggerReview onOpenTrigger={id => { opened = id }} onChanged={() => undefined} />)
  expect(ui.getByText(/3 missed fires/)).toBeInTheDocument()
  expect(ui.getByText(/may already have produced an effect/)).toBeInTheDocument()
  expect(ui.getAllByText(/run-workflow/)).toHaveLength(2)
  fireEvent.click(ui.getByRole('button', { name: 'Daily summary' }))
  expect(opened).toBe(missed.trigger_id)
  expect(reviewDecision(missed, 'run_now')).toEqual({ trigger_id: missed.trigger_id, review_id: missed.id, decision: 'run_now', expected_revision: missed.action_revision })
  expect(reviewDecision(interrupted, 'dismiss').expected_revision).toBe(interrupted.action_revision)
})
it('retains the actual card when the real API write cannot reach a gateway', async () => {
  let changes = 0
  const ui = render(<ReviewCard card={missed} onOpenTrigger={() => undefined} onChanged={() => { changes += 1 }} />)
  fireEvent.click(ui.getByRole('button', { name: 'Dismiss' }))
  await waitFor(() => expect(ui.getByRole('alert')).toBeInTheDocument())
  expect(ui.getByRole('button', { name: 'Daily summary' })).toBeInTheDocument()
  expect(ui.getByRole('button', { name: 'Dismiss' })).toBeEnabled()
  expect(changes).toBe(0)
})
