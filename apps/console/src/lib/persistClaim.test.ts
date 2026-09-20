import { describe, expect, it } from 'vitest'
import { persistClaim } from './persistClaim'

describe('persistClaim', () => {
  it('confirms persistence only from two explicit true values', () => {
    expect(persistClaim(true, true)).toBe(true)
    expect(persistClaim(true, false)).toBe(false)
    expect(persistClaim(true, undefined)).toBe(false)
    expect(persistClaim(false, true)).toBe(false)
  })
})
