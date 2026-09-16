import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { FeedbackProducerRow } from '../../shared/data/api'
import { FeedbackPanel } from './FeedbackPanel'


const feedbackProducers = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    feedbackProducers: (...a: unknown[]) => feedbackProducers(...a),
    feedbackSnooze: vi.fn(),
    feedbackClear: vi.fn(),
  },
}))
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))

function rows(...producers: FeedbackProducerRow[]) {
  feedbackProducers.mockResolvedValue({ producers, min_n: 5, window_days: 90 })
}

const UNENFORCED: FeedbackProducerRow = {
  producer_kind: 'prompt', producer_id: 'task-inbox-classify',
  ups: 3, downs: 6, n: 9, accuracy: 0.333, proposal_only: true,
}
const ENFORCED: FeedbackProducerRow = {
  producer_kind: 'skill_synthesis', producer_id: 'some-skill',
  ups: 1, downs: 8, n: 9, accuracy: 0.111, suppressed: true,
}

describe('the panel only claims "stopped surfacing" where something stops', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('a below-threshold producer with NO surfacing gate reads "retire proposed", never "suppressed"', async () => {
    rows(UNENFORCED)
    render(<FeedbackPanel />)
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    expect(screen.getByText('retire proposed')).toBeTruthy()
    expect(
      screen.queryByText('suppressed'),
      'a prompt producer is still injected verbatim — calling it suppressed tells the user its ' +
        'output stopped, which is the one thing that did not happen',
    ).toBeNull()
  })

  it('the honest pill explains WHY it still runs', async () => {
    rows(UNENFORCED)
    render(<FeedbackPanel />)
    const pill = await waitFor(() => screen.getByText('retire proposed'))
    expect(pill.getAttribute('title') ?? '').toMatch(/no surfacing gate|still runs/i)
  })

  it('an ENFORCED kind below threshold DOES read "suppressed"', async () => {
    rows(ENFORCED)
    render(<FeedbackPanel />)
    await waitFor(() => expect(screen.getByText('some-skill')).toBeTruthy())
    expect(screen.getByText('suppressed')).toBeTruthy()
    expect(screen.queryByText('retire proposed')).toBeNull()
  })

  it('the two states never render on one row', async () => {
    rows({ ...UNENFORCED, suppressed: false, proposal_only: true })
    render(<FeedbackPanel />)
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    expect(screen.queryAllByText(/^(suppressed|retire proposed)$/)).toHaveLength(1)
  })

  it('the panel HEADER does not promise a withholding only one kind gets', async () => {
    rows()
    render(<FeedbackPanel />)
    const hint = await waitFor(() => screen.getByText(/attributed to the source that produced it/))
    expect(hint.textContent ?? '').toMatch(/asks to be reviewed/)
    expect(
      hint.textContent ?? '',
      'unconditional "stops surfacing" holds for one of the six producer kinds',
    ).not.toMatch(/keeps being wrong stops surfacing/)
  })

  it('a healthy producer claims neither', async () => {
    rows({ ...UNENFORCED, accuracy: 0.95, proposal_only: undefined, suppressed: false })
    render(<FeedbackPanel />)
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    expect(screen.queryByText('suppressed')).toBeNull()
    expect(screen.queryByText('retire proposed')).toBeNull()
  })
})
