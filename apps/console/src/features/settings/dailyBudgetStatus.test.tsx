import { render, screen, cleanup } from '@testing-library/react'
import { afterEach, describe, it, expect } from 'vitest'
import { DailyBudgetStatus } from './UsagePanel'
import type { DailySpend } from '../../shared/data/api'
afterEach(cleanup)
const spend: DailySpend = { tokens: 12, dollars: 2, max_tokens: 100, max_dollars: 1, status: 'exceeded', reason: '', paused: false, paid_calls_paused: true, unpriced: 1, held_tokens: 4, held_dollars: .02, resumes_at: '2026-10-07T00:00:00Z' }
describe('daily budget truth', () => {
  it('uses actual automation meter, distinguishes unknown cost, and gives native actions', () => {
    render(<DailyBudgetStatus spend={spend} />)
    expect(screen.getByText('Dollar budget reached — free work can continue.')).toBeTruthy()
    expect(screen.getByText(/Cost is incomplete/)).toBeTruthy()
    expect(screen.getByText(/Running calls have set aside/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Set model prices' }).getAttribute('href')).toBe('#/settings/usage')
    expect(screen.getByRole('link', { name: 'Change budget limits' }).getAttribute('href')).toBe('#/settings/guardrails')
  })
  it('reports the token stop independently of a paid ceiling', () => {
    render(<DailyBudgetStatus spend={{ ...spend, paused: true, unpriced: 0 }} />)
    expect(screen.getByText('Token budget reached — unattended work is paused.')).toBeTruthy()
    expect(screen.queryByText(/Cost is incomplete/)).toBeNull()
  })
})
