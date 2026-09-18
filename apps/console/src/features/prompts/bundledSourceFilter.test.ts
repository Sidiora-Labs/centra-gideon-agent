import { describe, it, expect } from 'vitest'
import { selectPromptRows, type PromptLibraryRow } from './promptLibraryState'
import { isReadOnly, promptProvenance, sourceLabel } from './promptMeta'


const rows: PromptLibraryRow[] = [
  { name: 'triage', title: 'Triage', source: 'user', tags: ['system', 'bundled'] },
  { name: 'standup', title: 'Standup', source: 'user', tags: ['mine'] },
  { name: 'review', title: 'Review', source: 'marketplace', tags: ['bundled'] },
  { name: 'digest', title: 'Digest', source: 'user', tags: ['bundled'] },
  { name: 'notes', title: 'Notes' },
]
const names = (list: PromptLibraryRow[]) => list.map((r) => r.name)

describe('the Bundled source filter matches what the badge says', () => {
  it('returns exactly the rows the badge calls bundled', () => {
    const bundled = selectPromptRows(rows, '', 'name', 'bundled')
    expect(names(bundled)).toEqual(['digest', 'triage'])
    for (const row of bundled) expect(sourceLabel(row.source, row.tags)).toBe('bundled')
  })

  it('stops counting a bundled prompt as a user prompt — the two filters do not overlap', () => {
    expect(names(selectPromptRows(rows, '', 'name', 'user'))).toEqual(['notes', 'standup'])
    const user = new Set(names(selectPromptRows(rows, '', 'name', 'user')))
    for (const name of names(selectPromptRows(rows, '', 'name', 'bundled'))) expect(user.has(name)).toBe(false)
  })

  it('leaves a genuinely foreign source alone, bundled tag or not', () => {
    expect(names(selectPromptRows(rows, '', 'name', 'marketplace'))).toEqual(['review'])
    expect(names(selectPromptRows(rows, '', 'name', 'all'))).toHaveLength(rows.length)
  })

  it('still narrows by the search needle inside a source', () => {
    expect(names(selectPromptRows(rows, 'tri', 'name', 'bundled'))).toEqual(['triage'])
    expect(names(selectPromptRows(rows, 'standup', 'name', 'bundled'))).toEqual([])
  })
})

describe('the source sort groups by the same answer the badge prints', () => {
  it('keeps the bundled rows adjacent, ordered by the badge text', () => {
    const sorted = selectPromptRows(rows, '', 'source', 'all')
    expect(names(sorted)).toEqual(['digest', 'triage', 'review', 'notes', 'standup'])
    const labels = sorted.map((r) => sourceLabel(r.source, r.tags))
    expect(labels).toEqual(['bundled', 'bundled', 'marketplace', 'user', 'user'])
    expect([...labels].sort(), 'a group that splits is a sort that disagrees with the badge').toEqual(labels)
  })

  it('is the one resolver, not a second opinion', () => {
    for (const row of rows) expect(promptProvenance(row)).toBe(sourceLabel(row.source, row.tags))
  })
})

describe('naming a prompt bundled does not lock it', () => {
  it('leaves every bundled row editable, by raw source or by resolved provenance', () => {
    for (const row of selectPromptRows(rows, '', 'name', 'bundled')) {
      expect(isReadOnly(row.source), `${row.name} by source`).toBe(false)
      expect(isReadOnly(promptProvenance(row)), `${row.name} by provenance`).toBe(false)
    }
  })

  it('still locks a prompt that really is foreign', () => {
    expect(isReadOnly('marketplace')).toBe(true)
  })
})
