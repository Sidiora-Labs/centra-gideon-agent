import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
import { SessionSkillsReview } from '../../features/chat/SessionSkillsReview'

vi.mock('../data/api', () => ({
  api: {
    ephemeralSkills: vi.fn().mockResolvedValue([
      { slug: 'x', title: 'A skill', body: 'body', scope: 'session' },
    ]),
    promoteEphemeralSkill: vi.fn().mockResolvedValue({}),
    discardEphemeralSkill: vi.fn().mockResolvedValue({}),
  },
}))


describe('SessionSkillsReview closes on Escape', () => {
  const openSheet = async () => {
    const view = render(<SessionSkillsReview sessionKey="s" refreshKey={0} />)
    const trigger = await view.findByRole('button', { name: /session skill/i })
    fireEvent.click(trigger)
    await view.findByText('Skills taught this session')
    return view
  }

  it('Escape dismisses the sheet', async () => {
    const view = await openSheet()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(view.queryByText('Skills taught this session')).toBeNull()
  })

  it('an unrelated key does NOT dismiss it', async () => {
    const view = await openSheet()
    fireEvent.keyDown(document, { key: 'a' })
    fireEvent.keyDown(document, { key: 'Enter' })
    expect(view.queryByText('Skills taught this session')).not.toBeNull()
  })

  it('the listener is torn down when the sheet closes', async () => {
    const view = await openSheet()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(view.queryByText('Skills taught this session')).toBeNull()
    const trigger = await view.findByRole('button', { name: /session skill/i })
    fireEvent.click(trigger)
    await view.findByText('Skills taught this session')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(view.queryByText('Skills taught this session')).toBeNull()
  })
})
