import { describe, expect, it } from 'vitest'
import type { WorkflowNodeState } from '../../shared/data/api'
import { COL_GAP, NODE_H, NODE_W, ROW_GAP, dagState, isAwaitingHuman, layoutRunDag } from './runDag'


const node = (instance_path: string, state = 'done'): WorkflowNodeState => ({
  instance_path,
  node_id: (instance_path.split(/[.[]/).filter(Boolean).pop() ?? instance_path).replace(/]$/, ''),
  state,
})

const NESTED = [
  node('root'),
  node('root.children[0]'),
  node('root.children[1]'),
  node('root.children[1].children[0]'),
  node('root.children[1].children[1]'),
]

describe('layout', () => {
  it('places every node exactly once', () => {
    const out = layoutRunDag(NESTED)
    expect(out.nodes).toHaveLength(NESTED.length)
    expect(new Set(out.nodes.map((n) => n.id)).size).toBe(NESTED.length)
  })

  it('uses the instance PATH as the node id, not the node_id', () => {
    const out = layoutRunDag([node('root.body[0]'), node('root.body[1]')])
    expect(out.nodes.map((n) => n.id)).toEqual(['root.body[0]', 'root.body[1]'])
  })

  it('columns by DEPTH so nesting reads left to right', () => {
    const out = layoutRunDag(NESTED)
    const byId = Object.fromEntries(out.nodes.map((n) => [n.id, n]))
    expect(byId['root'].x).toBe(0)
    expect(byId['root.children[0]'].x).toBe(NODE_W + COL_GAP)
    expect(byId['root.children[1].children[0]'].x).toBe(2 * (NODE_W + COL_GAP))
  })

  it('stacks siblings DOWNWARD instead of overlapping them', () => {
    const out = layoutRunDag(NESTED)
    const byId = Object.fromEntries(out.nodes.map((n) => [n.id, n]))
    expect(byId['root.children[0]'].y).toBe(0)
    expect(byId['root.children[1]'].y).toBe(NODE_H + ROW_GAP)
  })

  it('draws a containment edge from each node to its nearest placed ancestor', () => {
    const ids = layoutRunDag(NESTED).edges.map((e) => e.id)
    expect(ids).toContain('root->root.children[1]')
    expect(ids).toContain('root.children[1]->root.children[1].children[0]')
  })

  it('finds a parent across BOTH path separators', () => {
    const out = layoutRunDag([node('root'), node('root.children[3]')])
    expect(out.edges.map((e) => e.id)).toEqual(['root->root.children[3]'])
  })

  it('SKIPS an edge when the ancestor is absent rather than pointing at nothing', () => {
    const out = layoutRunDag([node('root.children[1].children[0]')])
    expect(out.edges).toEqual([])
    expect(out.nodes).toHaveLength(1)
  })

  it('links to the NEAREST placed ancestor, not the textual parent', () => {
    const out = layoutRunDag([node('root'), node('root.children[1].children[0]')])
    expect(out.edges.map((e) => e.id)).toEqual(['root->root.children[1].children[0]'])
  })

  it('animates an edge only into RUNNING work', () => {
    const out = layoutRunDag([node('root'), node('root.children[0]', 'running')])
    expect(out.edges[0].active).toBe(true)
    expect(layoutRunDag([node('root'), node('root.children[0]', 'done')]).edges[0].active).toBe(false)
  })

  it('reports a ZERO size for an empty run', () => {
    expect(layoutRunDag([])).toEqual({ nodes: [], edges: [], width: 0, height: 0 })
  })

  it('sizes the canvas to the widest column and the tallest stack', () => {
    const out = layoutRunDag(NESTED)
    expect(out.width).toBe(3 * NODE_W + 2 * COL_GAP)
    expect(out.height).toBeGreaterThanOrEqual(2 * NODE_H + ROW_GAP)
  })

  it('renders the per-item label when a foreach node has one', () => {
    const out = layoutRunDag(
      [{ ...node('root.body[0]'), item_label: 'auth.py' }],
      { label: (n) => (n.item_label ? `${n.node_id} · ${n.item_label}` : n.node_id) },
    )
    expect(out.nodes[0].content).toBe('0 · auth.py')
  })
})

describe('state mapping', () => {
  it('treats the engine SUCCESS states as done', () => {
    expect(dagState('degraded')).toBe('done')
    expect(dagState('no_change')).toBe('done')
    expect(dagState('done')).toBe('done')
  })

  it('treats scope_violation and escalated as errors', () => {
    expect(dagState('scope_violation')).toBe('error')
    expect(dagState('escalated')).toBe('error')
  })

  it('separates blocked from awaiting', () => {
    expect(dagState('blocked')).toBe('blocked')
    expect(dagState('waiting')).toBe('awaiting')
  })

  it('maps an UNKNOWN state to the one that claims least', () => {
    expect(dagState('quantum')).toBe('todo')
  })
})

describe('answerability', () => {
  const gate = node('root.children[0]', 'waiting')

  it('is FALSE for a waiting node with no continuation', () => {
    expect(isAwaitingHuman(gate, [])).toBe(false)
  })

  it('is TRUE once a live continuation exists', () => {
    expect(isAwaitingHuman(gate, [{ instance_path: gate.instance_path }])).toBe(true)
  })

  it('is FALSE for an EXPIRED continuation', () => {
    expect(isAwaitingHuman(gate, [{ instance_path: gate.instance_path, expired: true }])).toBe(false)
  })

  it('is FALSE for a node that is not waiting at all', () => {
    expect(isAwaitingHuman(node('root.children[0]', 'running'), [
      { instance_path: 'root.children[0]' },
    ])).toBe(false)
  })

  it('marks an awaiting gate AWAITING even against the state map', () => {
    const out = layoutRunDag([gate], { continuations: [{ instance_path: gate.instance_path }] })
    expect(out.nodes[0].state).toBe('awaiting')
  })

  it('leaves a clock-parked wait node in its mapped state', () => {
    const out = layoutRunDag([gate], { continuations: [] })
    expect(out.nodes[0].state).toBe('awaiting')
    expect(isAwaitingHuman(gate, [])).toBe(false)
  })
})
