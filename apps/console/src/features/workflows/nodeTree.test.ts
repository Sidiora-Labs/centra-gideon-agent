import { describe, expect, it } from 'vitest'
import { buildTree, initialCollapsed, summarize, summaryLabel, visibleRows } from './nodeTree'
import type { WorkflowNodeState } from '../../shared/data/api'


const n = (path: string, state = 'done'): WorkflowNodeState => ({
  instance_path: path,
  node_id: path.split('.').pop() ?? path,
  state,
})

const REAL_RUN: WorkflowNodeState[] = [
  n('root.children[0]'),
  n('root.children[1]'),
  n('root.children[1].cases[deep]', 'skipped'),
  n('root.children[1].cases[deep].children[0]', 'skipped'),
  n('root.children[1].cases[deep].children[1]', 'skipped'),
  n('root.children[1].cases[standard]'),
  n('root.children[2]', 'running'),
]

describe('buildTree', () => {
  it('derives parenthood from the instance PATH', () => {
    const rows = buildTree(REAL_RUN)
    const branch = rows.find((r) => r.node.instance_path === 'root.children[1]')!
    expect(branch.descendants).toEqual([
      'root.children[1].cases[deep]',
      'root.children[1].cases[deep].children[0]',
      'root.children[1].cases[deep].children[1]',
      'root.children[1].cases[standard]',
    ])
  })

  it('sorts by path so the list reads in spec order', () => {
    const rows = buildTree([n('root.children[2]'), n('root.children[0]'), n('root.children[1]')])
    expect(rows.map((r) => r.node.instance_path)).toEqual([
      'root.children[0]', 'root.children[1]', 'root.children[2]',
    ])
  })

  it('a leaf has no descendants and is not collapsible', () => {
    const rows = buildTree(REAL_RUN)
    const leaf = rows.find((r) => r.node.instance_path === 'root.children[0]')!
    expect(leaf.descendants).toEqual([])
    expect(leaf.collapsible).toBe(false)
  })

  it('a container with ONE child is not collapsible', () => {
    const rows = buildTree([n('root.body'), n('root.body.children[0]')])
    expect(rows.find((r) => r.node.instance_path === 'root.body')!.collapsible).toBe(false)
  })

  it('handles an empty node list', () => {
    expect(buildTree([])).toEqual([])
  })
})

describe('summarize', () => {
  it('counts by state rather than reducing to a percentage', () => {
    const paths = ['root.body#0', 'root.body#1', 'root.body#2']
    const nodes = [n('root.body#0'), n('root.body#1', 'failed'), n('root.body#2', 'skipped')]
    const s = summarize(paths, nodes)
    expect(s.total).toBe(3)
    expect(s.byState).toEqual({ done: 1, failed: 1, skipped: 1 })
  })

  it('breaks a tie toward the most ALARMING state', () => {
    const nodes = [n('a', 'done'), n('b', 'failed')]
    expect(summarize(['a', 'b'], nodes).dominant).toBe('failed')
  })

  it('picks the most common state when there is no tie', () => {
    const nodes = [n('a', 'skipped'), n('b', 'skipped'), n('c', 'done')]
    expect(summarize(['a', 'b', 'c'], nodes).dominant).toBe('skipped')
  })

  it('ignores paths that are not in the node list', () => {
    expect(summarize(['nope'], [n('a')]).total).toBe(0)
  })
})

describe('initialCollapsed', () => {
  it('collapses a finished, untaken subgraph', () => {
    const rows = buildTree(REAL_RUN)
    const collapsed = initialCollapsed(rows, REAL_RUN)
    expect(collapsed.has('root.children[1]')).toBe(true)
  })

  it('does NOT collapse a subtree with live work', () => {
    const nodes = [
      n('root.body'),
      n('root.body.children[0]', 'done'),
      n('root.body.children[1]', 'running'),
    ]
    expect(initialCollapsed(buildTree(nodes), nodes).has('root.body')).toBe(false)
  })

  it('does NOT collapse a subtree containing a FAILURE', () => {
    const nodes = [
      n('root.body'),
      n('root.body.children[0]', 'done'),
      n('root.body.children[1]', 'failed'),
    ]
    expect(initialCollapsed(buildTree(nodes), nodes).has('root.body')).toBe(false)
  })

  it('does not collapse a scope violation or a blocked node either', () => {
    for (const bad of ['scope_violation', 'blocked']) {
      const nodes = [n('root.body'), n('root.body.children[0]', 'done'), n('root.body.children[1]', bad)]
      expect(initialCollapsed(buildTree(nodes), nodes).has('root.body')).toBe(false)
    }
  })

  it('DOES collapse a subtree that merely degraded', () => {
    const nodes = [
      n('root.body'),
      n('root.body.children[0]', 'done'),
      n('root.body.children[1]', 'degraded'),
    ]
    expect(initialCollapsed(buildTree(nodes), nodes).has('root.body')).toBe(true)
  })
})

describe('visibleRows', () => {
  it('hides everything under a collapsed container', () => {
    const rows = buildTree(REAL_RUN)
    const shown = visibleRows(rows, new Set(['root.children[1]']))
    expect(shown.map((r) => r.node.instance_path)).toEqual([
      'root.children[0]', 'root.children[1]', 'root.children[2]',
    ])
  })

  it('hides a GRANDCHILD under a collapsed grandparent', () => {
    const rows = buildTree(REAL_RUN)
    const shown = visibleRows(rows, new Set(['root.children[1]']))
    expect(shown.some((r) => r.node.instance_path.includes('cases[deep].children'))).toBe(false)
  })

  it('keeps the collapsed container itself visible', () => {
    const shown = visibleRows(buildTree(REAL_RUN), new Set(['root.children[1]']))
    expect(shown.some((r) => r.node.instance_path === 'root.children[1]')).toBe(true)
  })

  it('returns everything when nothing is collapsed', () => {
    const rows = buildTree(REAL_RUN)
    expect(visibleRows(rows, new Set())).toHaveLength(rows.length)
  })
})

describe('summaryLabel', () => {
  it('puts the most alarming state FIRST', () => {
    const nodes = [n('a'), n('b'), n('c', 'failed')]
    const label = summaryLabel(summarize(['a', 'b', 'c'], nodes))
    expect(label.startsWith('1 failed')).toBe(true)
  })

  it('humanizes an underscored state', () => {
    const label = summaryLabel(summarize(['a'], [n('a', 'scope_violation')]))
    expect(label).toBe('1 scope violation')
  })

  it('reads as a single line for the common untaken-branch case', () => {
    const paths = Array.from({ length: 18 }, (_, i) => `p${i}`)
    const nodes = paths.map((p) => n(p, 'skipped'))
    expect(summaryLabel(summarize(paths, nodes))).toBe('18 skipped')
  })
})
