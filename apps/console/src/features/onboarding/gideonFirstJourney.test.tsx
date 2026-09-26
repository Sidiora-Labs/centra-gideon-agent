import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FirstJourneyActions } from '../../app/shell/Onboarding'
import { clearOnboardingExit, peekOnboardingExit, setOnboardingExit } from './exitTo'

beforeEach(clearOnboardingExit)
afterEach(() => { cleanup(); clearOnboardingExit() })

describe('first journey exit', () => {
  it('saves the first chat route for the shell to open after setup persists', async () => {
    const exitTo = vi.fn(setOnboardingExit)
    const tour = vi.fn()
    render(<FirstJourneyActions exitTo={exitTo} tour={tour} />)
    await userEvent.click(screen.getByRole('button', { name: /Start a conversation/ }))
    expect(exitTo).toHaveBeenCalledExactlyOnceWith('chat/new')
    expect(peekOnboardingExit()).toBe('chat/new')
    expect(tour).not.toHaveBeenCalled()
  })

  it('keeps the optional real product tour action separate', async () => {
    const exitTo = vi.fn(setOnboardingExit)
    const tour = vi.fn()
    render(<FirstJourneyActions exitTo={exitTo} tour={tour} />)
    await userEvent.click(screen.getByRole('button', { name: /Take the quick tour/ }))
    expect(tour).toHaveBeenCalledOnce()
    expect(exitTo).not.toHaveBeenCalled()
    expect(peekOnboardingExit()).toBe('')
  })

  it('lets keyboard users start the first conversation', async () => {
    const exitTo = vi.fn(setOnboardingExit)
    render(<FirstJourneyActions exitTo={exitTo} tour={vi.fn()} />)
    screen.getByRole('button', { name: /Start a conversation/ }).focus()
    await userEvent.keyboard('{Enter}')
    expect(exitTo).toHaveBeenCalledExactlyOnceWith('chat/new')
  })
})
