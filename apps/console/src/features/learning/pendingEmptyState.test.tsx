import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { LearningEmptyState } from './LearningPage'


describe('learning proposal empty state', () => {
  beforeEach(() => {
    window.location.hash = '#/learning'
  })

  it('surfaces pending skill proposals instead of claiming there is nothing to review', () => {
    render(<LearningEmptyState pendingSkillProposals={2} />)

    expect(screen.getByRole('heading', { name: '2 skill proposals await review' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Nothing to review' })).toBeNull()
    expect(screen.getByText(/skill proposal queue still needs your review/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Review skill proposals' }))
    expect(window.location.hash).toBe('#/skills?mode=proposals')
  })

  it('only claims there is nothing to review after both proposal queues are known empty', () => {
    render(<LearningEmptyState pendingSkillProposals={0} />)

    expect(screen.getByRole('heading', { name: 'Nothing to review' })).toBeInTheDocument()
  })

  it('does not turn a failed skill-proposal check into a false empty state', () => {
    render(<LearningEmptyState pendingSkillProposals={undefined} pendingSkillProposalsError={new Error('offline')} />)

    expect(screen.getByRole('heading', { name: 'No proposals in this learning queue' })).toBeInTheDocument()
    expect(screen.getByText(/there may still be proposals awaiting review/i)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Nothing to review' })).toBeNull()
  })
})
