import { describe, it, expect } from 'vitest'
import { fvs, withWeight } from './fontWeight'


describe('fvs', () => {
  it('returns the exact inline style the call sites hand-write', () => {
    expect(fvs(500)).toEqual({ fontVariationSettings: '"wght" 500' })
    expect(fvs(600)).toEqual({ fontVariationSettings: '"wght" 600' })
    expect(fvs(470)).toEqual({ fontVariationSettings: '"wght" 470' })
  })

  it('withWeight merges onto an existing style without dropping props', () => {
    expect(withWeight({ color: 'var(--color-primary)' }, 550)).toEqual({
      color: 'var(--color-primary)',
      fontVariationSettings: '"wght" 550',
    })
    expect(withWeight(undefined, 400)).toEqual({ fontVariationSettings: '"wght" 400' })
  })
})
