import { describe, it, expect } from 'vitest'
import { isReadOnly, sourceLabel, sourceTone } from './promptMeta'


const BUNDLED = ['system', 'bundled']

describe('the source pill names a shipped prompt as bundled', () => {
  it('a seeded prompt says bundled, not user', () => {
    expect(sourceLabel('user', BUNDLED)).toBe('bundled')
  })

  it('a prompt the user actually wrote still says user', () => {
    expect(sourceLabel('user', ['mine'])).toBe('user')
    expect(sourceLabel('user', [])).toBe('user')
    expect(sourceLabel('user')).toBe('user')
    expect(sourceLabel(undefined)).toBe('user')
  })

  it('a genuinely non-user source keeps its own name, tags or no tags', () => {
    expect(sourceLabel('marketplace', BUNDLED)).toBe('marketplace')
    expect(sourceLabel('marketplace')).toBe('marketplace')
  })

  it('the relabel does not make a shipped prompt read-only', () => {
    expect(isReadOnly('user')).toBe(false)
  })

  it('the relabel does not dim a shipped prompt to the read-only tone', () => {
    expect(sourceTone('user')).toBe('var(--color-primary)')
    expect(sourceTone('marketplace')).toBe('var(--color-info)')
  })
})
