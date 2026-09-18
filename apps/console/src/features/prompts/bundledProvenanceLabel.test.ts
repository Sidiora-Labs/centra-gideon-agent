import { describe, it, expect } from 'vitest'
import { isReadOnly, promptProvenance, sourceLabel, sourceTone } from './promptMeta'


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

  it('the pill reads the one resolver — a row and its parts answer the same', () => {
    expect(promptProvenance({ source: 'user', tags: BUNDLED })).toBe('bundled')
    expect(promptProvenance({ source: 'user', tags: ['mine'] })).toBe('user')
    expect(promptProvenance({})).toBe('user')
    expect(promptProvenance({ source: 'marketplace', tags: BUNDLED })).toBe('marketplace')
    for (const row of [{ source: 'user', tags: BUNDLED }, { source: 'marketplace' }, {}]) {
      expect(sourceLabel(row.source, row.tags)).toBe(promptProvenance(row))
    }
  })

  it('a row resolved as bundled stays editable — provenance is not a lock', () => {
    expect(isReadOnly(promptProvenance({ source: 'user', tags: BUNDLED }))).toBe(false)
  })
})
