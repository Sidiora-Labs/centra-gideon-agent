import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { DecisionJournal } from './DecisionJournal'
import {
  bucketLabel,
  bucketPlottable,
  calibrationCaption,
  calibrationState,
  confidenceLabel,
  gradeLabel,
  horizonLabel,
  pendingState,
} from './decisionMeta'
import { resolveType, typeLabel } from './knowledgeMeta'
import { api, type CalibrationBucket, type DecisionJournalView, type DecisionRow } from '../../shared/data/api'
import { invalidateKeys } from '../../shared/data/data'


function bucket(over: Partial<CalibrationBucket> = {}): CalibrationBucket {
  return { n: 12, better: 3, as_expected: 7, worse: 2, mean_confidence: 0.71, as_expected_rate: 0.583, count_honest: true, ...over }
}

function decision(over: Partial<DecisionRow> = {}): DecisionRow {
  return {
    id: 'dec-1',
    summary: 'Take the contract over the salaried role',
    status: 'pending',
    domain: 'career',
    expectation: 'I will earn more and regret the lost benefits by month six',
    confidence: 0.7,
    review_horizon: '2099-01-01T00:00:00',
    reminder_trigger_id: 'system:decision-journal:dec-1',
    deferrals: 0,
    stale_pending: false,
    outcome: null,
    outcome_grade: null,
    outcome_captured_at: null,
    lesson_memory_key: null,
    created_at: '2026-01-01T00:00:00',
    overdue: false,
    ...over,
  }
}

function view(over: Partial<DecisionJournalView> = {}): DecisionJournalView {
  return {
    decisions: [],
    calibration: {},
    calibration_min_n: 10,
    statuses: ['pending', 'resolved', 'abandoned'],
    domains: ['career', 'health', 'other'],
    grades: ['better', 'as_expected', 'worse'],
    ...over,
  }
}

afterEach(() => { vi.restoreAllMocks(); invalidateKeys('knowledge:decisions') })

describe('the calibration strip keeps three different truths apart', () => {
  const enough = view({ calibration: { career: bucket() }, decisions: [decision({ status: 'resolved' })] })
  const tooFew = view({ calibration: { career: bucket({ n: 3, better: 1, as_expected: 1, worse: 1, as_expected_rate: 0.333, count_honest: false }) } })
  const none = view({ calibration: {} })

  it('reads them as three distinct states', () => {
    expect(calibrationState(enough.calibration)).toBe('calibrated')
    expect(calibrationState(tooFew.calibration)).toBe('too-few')
    expect(calibrationState(none.calibration)).toBe('no-data')
  })

  it('says three PAIRWISE DIFFERENT things, so no two inputs read alike', () => {
    const said = [calibrationCaption(enough), calibrationCaption(tooFew), calibrationCaption(none)]
    expect(new Set(said).size).toBe(3)
    expect(said[0]).toMatch(/at least 10 resolved decisions/)
    expect(said[1]).toMatch(/too few to mean much/)
    expect(said[2]).toMatch(/No decisions logged yet/)
  })

  it('never reports a rate for a bucket the backend called dishonest', () => {
    const b = bucket({ n: 3, as_expected_rate: 0.333, count_honest: false })
    const label = bucketLabel(b, 10)
    expect(label).toBe('3 of 10 decisions — too few to mean much')
    expect(label).not.toMatch(/33/)
    expect(label).not.toMatch(/%/)
  })

  it('reports the rate once the backend calls the bucket honest', () => {
    expect(bucketLabel(bucket(), 10)).toBe('12 decisions · 58% resolved as expected · 71% mean confidence')
  })

  it('refuses to plot a bar below the threshold', () => {
    expect(bucketPlottable(bucket())).toBe(true)
    expect(bucketPlottable(bucket({ count_honest: false }))).toBe(false)
    expect(bucketPlottable(bucket({ as_expected_rate: null }))).toBe(false)
  })

  it('distinguishes "nothing resolved yet" from "nothing logged at all"', () => {
    const open = view({ decisions: [decision(), decision({ id: 'dec-2' })] })
    expect(calibrationState(open.calibration)).toBe('no-data')
    expect(calibrationCaption(open)).toMatch(/2 decisions still open and none resolved yet/)
    expect(calibrationCaption(open)).not.toBe(calibrationCaption(none))
  })

  it('an unstated confidence is not a confidence of zero', () => {
    expect(confidenceLabel(decision({ confidence: null }))).toBe('no stated confidence')
    expect(confidenceLabel(decision({ confidence: 0.7 }))).toBe('70% confident')
    expect(bucketLabel(bucket({ mean_confidence: null }), 10)).toMatch(/no stated confidence/)
  })
})

describe('a pending decision says which of three things is true about its review', () => {
  const now = new Date('2026-06-01T00:00:00Z')

  it('counts down while the horizon is ahead', () => {
    const d = decision({ review_horizon: '2026-06-08T00:00:00Z' })
    expect(pendingState(d)).toBe('counting')
    expect(horizonLabel(d, now)).toBe('Review in 7 days')
  })

  it('flags an overdue horizon', () => {
    const d = decision({ review_horizon: '2026-05-25T00:00:00Z', overdue: true })
    expect(pendingState(d)).toBe('overdue')
    expect(horizonLabel(d, now)).toBe('Review was due 7 days ago')
  })

  it('says a stale-pending decision has NO reminder left, not merely that it is late', () => {
    const d = decision({ review_horizon: '2026-05-25T00:00:00Z', overdue: true, stale_pending: true, deferrals: 2 })
    expect(pendingState(d)).toBe('stale')
    expect(horizonLabel(d, now)).toBe('Review lapsed 7 days ago · deferred 2 times, no reminder left')
    expect(horizonLabel(d, now)).not.toBe(horizonLabel(decision({ review_horizon: '2026-05-25T00:00:00Z', overdue: true }), now))
  })

  it('does not claim a lapse when a stale decision’s horizon is still ahead', () => {
    const ahead = decision({ review_horizon: '2026-11-02T00:00:00Z', overdue: false, stale_pending: true, deferrals: 2 })
    expect(pendingState(ahead)).toBe('stale')
    const said = horizonLabel(ahead, now)
    expect(said).toBe('Deferred 2 times — no reminder left, so nothing will bring this back')
    expect(said).not.toMatch(/lapsed/)
    expect(said).not.toMatch(/ago/)
    expect(said).toMatch(/no reminder left/)
    expect(horizonLabel(decision({ review_horizon: '2026-05-25T00:00:00Z', stale_pending: true, deferrals: 2 }), now)).toMatch(/no reminder left/)
  })

  it('an unparseable horizon says nothing rather than NaN days', () => {
    expect(horizonLabel(decision({ review_horizon: '' }), now)).toBe('')
    expect(horizonLabel(decision({ review_horizon: 'soon' }), now)).toBe('')
  })
})

describe('the journal renders what the one read path returned', () => {
  it('shows the strip, the open decisions and the resolved ones side by side', async () => {
    vi.spyOn(api, 'decisionJournal').mockResolvedValue(view({
      calibration: { career: bucket() },
      decisions: [
        decision(),
        decision({
          id: 'dec-2', status: 'resolved', summary: 'Move to the coast',
          expectation: 'I will miss the city within a year',
          outcome: 'I did not miss it at all', outcome_grade: 'better',
          lesson_memory_key: 'lesson.abc123def456',
        }),
      ],
    }))
    render(<DecisionJournal onOpenItem={() => {}} onOpenChat={() => {}} />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Calibration' })).toBeTruthy())
    expect(screen.getByText(/12 decisions · 58% resolved as expected/)).toBeTruthy()
    expect(screen.getByText(/I will miss the city within a year/)).toBeTruthy()
    expect(screen.getByText(/I did not miss it at all/)).toBeTruthy()
    expect(screen.getByText(/lesson recorded/)).toBeTruthy()
    expect(screen.getByText(/Review in/)).toBeTruthy()
  })

  it('draws NO bar and says why when every domain is under the threshold', async () => {
    vi.spyOn(api, 'decisionJournal').mockResolvedValue(view({
      calibration: { career: bucket({ n: 4, better: 1, as_expected: 2, worse: 1, as_expected_rate: 0.5, count_honest: false }) },
      decisions: [decision({ status: 'resolved', outcome: 'fine', outcome_grade: 'as_expected' })],
    }))
    render(<DecisionJournal onOpenItem={() => {}} onOpenChat={() => {}} />)
    await waitFor(() => expect(screen.getByText(/No domain has reached 10 yet/)).toBeTruthy())
    expect(screen.getByText('4 of 10 decisions — too few to mean much')).toBeTruthy()
    expect(screen.getByText(/Not enough resolved decisions in this domain to draw a rate/)).toBeTruthy()
    expect(screen.queryByText(/50%/)).toBeNull()
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('says the strip has nothing to calibrate from rather than drawing an empty one', async () => {
    vi.spyOn(api, 'decisionJournal').mockResolvedValue(view({ decisions: [decision()] }))
    render(<DecisionJournal onOpenItem={() => {}} onOpenChat={() => {}} />)
    await waitFor(() => expect(screen.getByText(/none resolved yet/)).toBeTruthy())
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('says the grade in words, and an unknown one as ungraded rather than as a grade', async () => {
    expect(gradeLabel('as_expected')).toBe('as expected')
    expect(gradeLabel('better')).toBe('better than expected')
    expect(gradeLabel('worse')).toBe('worse than expected')
    expect(gradeLabel(null)).toBe('ungraded')
    expect(gradeLabel('invented_grade')).toBe('ungraded')
    expect(gradeLabel('invented_grade')).not.toBe(gradeLabel('as_expected'))
  })

  it('a decision resolved without a grade reads as ungraded, never as the middle grade', async () => {
    vi.spyOn(api, 'decisionJournal').mockResolvedValue(view({
      decisions: [decision({ id: 'dec-3', status: 'resolved', outcome: 'hard to say', outcome_grade: null })],
    }))
    render(<DecisionJournal onOpenItem={() => {}} onOpenChat={() => {}} />)
    await waitFor(() => expect(screen.getByText('ungraded')).toBeTruthy())
    expect(screen.queryByText('as expected')).toBeNull()
    expect(screen.queryByText('as_expected')).toBeNull()
  })

  it('a missing lesson is stated, not hidden', async () => {
    vi.spyOn(api, 'decisionJournal').mockResolvedValue(view({
      decisions: [decision({ status: 'resolved', outcome: 'went badly', outcome_grade: 'worse', lesson_memory_key: null })],
    }))
    render(<DecisionJournal onOpenItem={() => {}} onOpenChat={() => {}} />)
    await waitFor(() => expect(screen.getByText(/no lesson recorded/)).toBeTruthy())
  })

  it('a failed read is an error, never an empty journal', async () => {
    vi.spyOn(api, 'decisionJournal').mockRejectedValue(new Error('knowledge.db is locked'))
    render(<DecisionJournal onOpenItem={() => {}} onOpenChat={() => {}} />)
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(screen.getByText(/knowledge.db is locked/)).toBeTruthy()
    expect(screen.queryByText(/No decisions logged yet/)).toBeNull()
  })

  it('an empty journal points at the one place a decision can be made', async () => {
    vi.spyOn(api, 'decisionJournal').mockResolvedValue(view())
    render(<DecisionJournal onOpenItem={() => {}} onOpenChat={() => {}} />)
    await waitFor(() => expect(screen.getByText(/No decisions logged yet/)).toBeTruthy())
    expect(screen.getByRole('button', { name: /Open chat/ })).toBeTruthy()
    let opened = 0
    cleanup()
    render(<DecisionJournal onOpenItem={() => {}} onOpenChat={() => { opened += 1 }} />)
    ;(await screen.findByRole('button', { name: /Open chat/ })).click()
    expect(opened).toBe(1)
  })
})

describe('a decision in the library reads as a Decision', () => {
  it('resolves by the vision type and by the raw item_type', () => {
    expect(typeLabel({ type: 'decision' } as never)).toBe('Decision')
    expect(typeLabel({ item_type: 'decision' } as never)).toBe('Decision')
    expect(resolveType({ type: 'decision' } as never).key).toBe('decision')
    expect(typeLabel({ type: 'note' } as never)).toBe('Note')
  })
})
