import { describe, expect, it } from 'vitest'
import { CRON_EXPR_INVALID_REASON, cronExprInvalidReason } from './cronExpr'

describe('cronExprInvalidReason', () => {
  it.each([
    '0 9 * * *',
    '*/15 9-17 * JAN,MAR MON-FRI',
    '0 0 L * *',
    '0 0 1W * *',
    '0 0 * * MON#2',
    '0 0 * * L5',
    '@hourly',
  ])('accepts croniter grammar: %s', (expression) => {
    expect(cronExprInvalidReason(expression)).toBeUndefined()
  })

  it.each([
    '',
    '0 9 * *',
    '60 9 * * *',
    '0 24 * * *',
    '0 9 0 * *',
    '0 9 * 13 *',
    '0 9 * * 8',
    '0 9 * * FUNDAY',
    '0/0 9 * * *',
    '0 0 L/2 * *',
  ])('rejects expressions croniter rejects: %s', (expression) => {
    expect(cronExprInvalidReason(expression)).toBe(CRON_EXPR_INVALID_REASON)
  })
})
