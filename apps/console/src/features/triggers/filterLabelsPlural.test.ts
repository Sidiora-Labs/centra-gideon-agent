import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const page = () => readFileSync(join(SRC, 'features/triggers/TriggersListPage.tsx'), 'utf8')

describe('the triggers filter labels are plural categories', () => {
  it('every non-"All" filter chip is plural — none is a bare singular kind', () => {
    const src = page()
    const block = src.match(/const FILTERS[\s\S]*?\]\n/)?.[0] ?? ''
    const labels = [...block.matchAll(/label: '([^']+)'/g)].map((m) => m[1])
    expect(labels, 'the FILTERS census must not go empty').toEqual(
      ['All', 'Schedules', 'Lifecycle events', 'Data events', 'Automations'],
    )
    expect(labels, 'the lifecycle chip must be the plural category').not.toContain('Lifecycle')
  })

  it('each plural label stays attached to the kind used by the live filter', () => {
    const src = page()
    const block = src.match(/const FILTERS[\s\S]*?\]\n/)?.[0] ?? ''
    const pairs = [...block.matchAll(/key: '([^']+)', label: '([^']+)'/g)]
      .map(m => [m[1], m[2]])
    expect(pairs).toEqual([
      ['all', 'All'], ['schedule', 'Schedules'], ['lifecycle', 'Lifecycle events'],
      ['event', 'Data events'], ['store', 'Automations'],
    ])
    expect(src).toMatch(/filter === 'all' \|\| t\.kind === filter/)
    expect(src).toMatch(/onChange: setFilter/)
    expect(src).toMatch(/options: FILTERS\.map/)
  })
})
