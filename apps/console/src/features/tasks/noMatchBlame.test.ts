import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const src = readFileSync(join(process.cwd(), "src/features/tasks/TasksListPage.tsx"), 'utf8')

describe('the tasks no-match state names its narrower', () => {
  it('the blame-everything sentence is gone from the filtered branch', () => {
    expect(src).not.toMatch(/No tasks match this filter\./)
  })

  it('a search that narrows to nothing names the query and offers Clear search', () => {
    expect(src).toMatch(/No tasks match “\$\{q\}”/)
    expect(src).toMatch(/label: 'Clear search', onClick: \(\) => setQ\(''\)/)
  })

  it("the view escape resets EVERY in-page narrower, not just the status filter", () => {
    expect(src).toMatch(/setFilter\('all'\); setListFilter\(null\); setAssigned\(ASSIGNED_EVERYONE\)/)
  })

  it('both no-match variants count what is really there', () => {
    const counts = src.match(/You have \$\{tasks\?\.length \?\? 0\} task/g) ?? []
    expect(counts.length).toBeGreaterThanOrEqual(2)
  })
})
