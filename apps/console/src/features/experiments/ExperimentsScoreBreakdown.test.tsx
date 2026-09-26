import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ExperimentCampaign } from '../../shared/data/api'
import { ScoreBreakdown } from '../../shared/vendor/assistant-ui/elements/score-breakdown'
import { ExperimentsPage } from './ExperimentsPage'

const recorded: ExperimentCampaign = {
  id: 'campaign-1', title: 'Latency trial', objective: 'Reduce observed latency',
  workflow_name: 'respond', metric: 'latency', direction: 'minimize',
  max_parallel: 2, max_tokens: 1000, total_tokens: 420, status: 'active',
  best_attempt: 1, created_at: '2026-09-26T00:00:00Z',
  attempts: [
    { ordinal: 0, inputs: { prompt: 'A' }, state: 'complete', run_id: 'run-a',
      score: 32.125, valid: false, observation: 'Too slow', tokens: 200 },
    { ordinal: 1, inputs: { prompt: 'B' }, state: 'complete', run_id: 'run-b',
      score: -5.25, valid: true, observation: 'Measured below baseline', tokens: 220 },
    { ordinal: 2, inputs: { prompt: 'C' }, state: 'queued', run_id: '',
      score: null, valid: null, observation: '' },
  ],
}

let campaign: ExperimentCampaign
let observed: { id: string; ordinal: number; body: { score: number; valid: boolean; observation: string } } | null

vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      experimentCampaigns: async () => ({ campaigns: [campaign] }),
      experimentCampaign: async () => campaign,
      workflowDefs: async () => ({ defs: [] }),
      observeExperimentAttempt: async (id: string, ordinal: number, body: { score: number; valid: boolean; observation: string }) => {
        observed = { id, ordinal, body }
        return campaign
      },
    },
  }
})

beforeEach(() => { campaign = recorded; observed = null })
afterEach(cleanup)

function page(navigate = vi.fn()) {
  render(<ExperimentsPage sub="campaign-1" navigate={navigate} navEpoch={0} query={{}} setQuery={() => {}} />)
  return navigate
}

describe('recorded campaign scores in the real Experiments page', () => {
  it('shows exact arbitrary scores and recorded validity without a denominator, weight, meter, or verdict', async () => {
    const navigate = page()
    const group = await screen.findByRole('group', { name: 'Recorded attempt scores' })
    expect(screen.getByText('Recorded latency scores')).toBeTruthy()
    expect(within(group).getByText('Attempt #1')).toBeTruthy()
    expect(within(group).getByText('32.125')).toBeTruthy()
    expect(within(group).getByText('Invalid')).toBeTruthy()
    expect(within(group).getByText('Attempt #2')).toBeTruthy()
    expect(within(group).getByText('-5.25')).toBeTruthy()
    expect(within(group).getByText('Valid')).toBeTruthy()
    expect(within(group).queryByText('Attempt #3')).toBeNull()
    expect(group.querySelector('[role="meter"]')).toBeNull()
    expect(group.textContent).not.toContain('×')
    expect(group.textContent).not.toContain('/ 100')
    expect(group.textContent).not.toMatch(/pass|fail/i)
    expect(screen.getByText('Best valid attempt: #2')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Launch queued attempts' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'View run run-a' }))
    expect(navigate).toHaveBeenCalledWith('workflows/runs/run-a')
    fireEvent.click(screen.getByRole('button', { name: 'All campaigns' }))
    expect(navigate).toHaveBeenCalledWith('experiments')
  })

  it('keeps saving the recorded attempt score, validity, and observation', async () => {
    page()
    await screen.findByRole('group', { name: 'Recorded attempt scores' })
    fireEvent.click(screen.getAllByRole('button', { name: 'Save observation' })[0])
    await waitFor(() => expect(observed).toEqual({
      id: 'campaign-1', ordinal: 0,
      body: { score: 32.125, valid: false, observation: 'Too slow' },
    }))
  })

  it('does not render a scored breakdown for attempts with no recorded score', async () => {
    campaign = { ...recorded, best_attempt: null, attempts: recorded.attempts.map(attempt => ({
      ...attempt, score: null, valid: null,
    })) }
    page()
    expect(await screen.findByText('No valid scored result yet.')).toBeTruthy()
    expect(screen.queryByRole('group', { name: 'Recorded attempt scores' })).toBeNull()
    expect(screen.getByText('Attempt #1')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Launch queued attempts' })).toBeTruthy()
  })

  it('shows a recorded zero score while leaving unknown validity unknown', async () => {
    campaign = { ...recorded, best_attempt: null, attempts: recorded.attempts.map(attempt =>
      attempt.ordinal === 2
        ? { ...attempt, state: 'complete', score: 0, valid: null }
        : { ...attempt, score: null, valid: null },
    ) }
    page()
    const group = await screen.findByRole('group', { name: 'Recorded attempt scores' })
    expect(within(group).getByText('Attempt #3')).toBeTruthy()
    expect(within(group).getByText('0')).toBeTruthy()
    expect(within(group).getByText('Validity not recorded')).toBeTruthy()
    expect(group.querySelector('[role="meter"]')).toBeNull()
    expect(screen.getByText('No valid scored result yet.')).toBeTruthy()
  })
})

describe('ScoreBreakdown optional measurement fields', () => {
  it('shows an exact unbounded score without a guessed ratio or weighting', () => {
    const { container } = render(<ScoreBreakdown criteria={[
      { label: 'Attempt #7', score: 123.456, note: 'Validity not recorded' },
    ]} visibleCount={1} />)
    expect(screen.getByText('123.456')).toBeTruthy()
    expect(screen.getByText('Validity not recorded')).toBeTruthy()
    expect(container.querySelector('[role="meter"]')).toBeNull()
    expect(container.textContent).not.toContain('×')
    expect(container.textContent).not.toContain(' / ')
  })

  it('keeps an explicitly supplied verdict neutral when a ratio is unknown', () => {
    render(<ScoreBreakdown verdict="Reviewed" total={32.125} criteria={[]} visibleCount={0} />)
    expect(screen.getByText('32.125')).toBeTruthy()
    expect(screen.getByText('Reviewed').className).toContain('bg-foreground/[0.06]')
    expect(screen.queryByText(/\/ 100/)).toBeNull()
  })

  it('retains the original weighted meter when all measured inputs are supplied', () => {
    render(<ScoreBreakdown verdict="Measured" total={7.5} outOf={10} criteria={[
      { label: 'Accuracy', score: 7.5, weight: 2, note: 'Held-out review' },
    ]} visibleCount={1} />)
    expect(screen.getByText('/ 10')).toBeTruthy()
    expect(screen.getByText('×2')).toBeTruthy()
    expect(screen.getByText('Held-out review')).toBeTruthy()
    expect(screen.getByRole('meter', { name: 'Accuracy score' })).toHaveAttribute('aria-valuenow', '75')
    expect(screen.getByText('Measured').className).toContain('bg-emerald-500/12')
  })
})
