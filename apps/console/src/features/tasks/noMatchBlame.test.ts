import ts from 'typescript'
import { namedOwner, nodes } from '../../shared/testing/sourceOwners'
import { jsxTags } from '../../shared/testing/jsxContracts'
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
    const clear = namedOwner(src, 'clearNarrowing')
    const calls = nodes(clear, (node) => ts.isCallExpression(node)).map((node) => node.getText())
    for (const reset of ["setQ('')", "setFilter('all')", "setScope('')", "setTag('')", 'setListFilter(null)', 'setAssigned(ASSIGNED_EVERYONE)']) expect(calls, `reset ${reset}`).toContain(reset)
    const escape = jsxTags(namedOwner(src, 'noMatch'), ['EmptyState']).find((tag) => tag.attributes.get('action')?.includes("label: 'View all tasks'"))
    expect(escape?.attributes.get('action')).toContain('onClick: clearNarrowing')
  })

  it('both no-match variants count what is really there', () => {
    const counts = src.match(/You have \$\{tasks\?\.length \?\? 0\} task/g) ?? []
    expect(counts.length).toBeGreaterThanOrEqual(2)
  })
})
