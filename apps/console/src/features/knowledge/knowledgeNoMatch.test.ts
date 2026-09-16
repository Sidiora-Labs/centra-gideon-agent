import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const src = readFileSync(join(process.cwd(), "src/features/knowledge/KnowledgeListPage.tsx"), 'utf8')

describe('knowledge library no-match split', () => {
  it('the blame-everything sentence is gone', () => {
    expect(src).not.toContain('Try a different search or filter.')
  })

  it('a search miss names the query and offers Clear search (clearing BOTH query states)', () => {
    expect(src).toMatch(/No items match “\$\{submitted\}”/)
    expect(src).toMatch(/label: 'Clear search', onClick: \(\) => \{ setQ\(''\); setSubmitted\(''\) \}/)
  })

  it('the view escape resets every client-side narrower', () => {
    expect(src).toMatch(
      /label: 'View all items', onClick: \(\) => \{ setTypeFilter\(''\); setProviderFilter\(''\); setTagFilter\(''\); setCurationFilter\(''\) \}/,
    )
  })

  it('the empty shelf keeps a neutral sentence — navigation has no in-page escape', () => {
    expect(src).toMatch(/This shelf has no items yet\./)
  })

  it('the genuinely-empty library state is untouched', () => {
    expect(src).toContain('Knowledge base is empty')
  })
})
