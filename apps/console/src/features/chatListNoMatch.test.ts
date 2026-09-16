import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const src = readFileSync(join(process.cwd(), "src/features/ChatPage.tsx"), 'utf8')

describe('chat list no-match split', () => {
  it('the blame-everything sentence is gone', () => {
    expect(src).not.toContain('Try a different search or tag filter.')
  })

  it('a search miss names the query and offers Clear search', () => {
    expect(src).toMatch(/No chats match “\$\{q\.trim\(\)\}”/)
    expect(src).toMatch(/label: 'Clear search', onClick: \(\) => setQ\(''\)/)
  })

  it('the view escape resets BOTH remaining narrowers — tags and scope', () => {
    expect(src).toMatch(
      /label: 'View all chats', onClick: \(\) => \{ setTagFilter\(new Set\(\)\); setOrigin\('all'\) \}/,
    )
  })

  it('both variants count the loaded chats, so the state cannot read as "you have none"', () => {
    const counts = src.match(/You have \$\{sessions\.length\} chat/g) ?? []
    expect(counts.length).toBeGreaterThanOrEqual(2)
  })
})
