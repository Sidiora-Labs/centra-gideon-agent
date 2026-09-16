import type { TaskItem } from '../../shared/data/api'
import type { DagEdge } from './DagView'
import { cyclicNodes, depMap, layeredLayout } from './dag'

export const GRAPH_BOX = { width: 210, height: 58, rowGap: 64, columnGap: 28, padding: 24, radius: 12 }
export function placeTaskGraph(tasks: TaskItem[], availableWidth: number) {
  const box = GRAPH_BOX
  const graph = depMap(tasks)
  const layout = layeredLayout(graph)
  const cyclic = cyclicNodes(graph)
  const inner = Math.max(box.width, (availableWidth || 960) - 2 * box.padding)
  const capacity = Math.max(1, Math.floor((inner + box.columnGap) / (box.width + box.columnGap)))
  const groups: string[][] = []
  for (const node of layout.nodes.values()) (groups[node.layer] ??= []).push(node.id)
  const indexed = new Map(tasks.map(task => [task.id, task]))
  const positions = new Map<string, { task: TaskItem; x: number; y: number }>()
  let row = 0
  for (const group of groups) {
    if (!group) continue
    for (let start = 0; start < group.length; start += capacity) {
      const ids = group.slice(start, start + capacity)
      const occupied = ids.length * box.width + (ids.length - 1) * box.columnGap
      const left = box.padding + Math.max(0, (inner - occupied) / 2)
      ids.forEach((id, column) => { const task = indexed.get(id); if (task) positions.set(id, { task, x: left + column * (box.width + box.columnGap), y: box.padding + row * (box.height + box.rowGap) }) })
      row++
    }
  }
  const edges: DagEdge[] = []
  for (const [id, prerequisites] of graph) {
    const destination = positions.get(id)
    if (!destination) continue
    for (const prerequisite of prerequisites) {
      const source = positions.get(prerequisite)
      if (!source) continue
      edges.push({ id: `${prerequisite}->${id}`, from: prerequisite, to: id, x1: source.x + box.width / 2, y1: source.y + box.height, x2: destination.x + box.width / 2, y2: destination.y, bad: cyclic.has(prerequisite) && cyclic.has(id), active: source.task.status === 'done' && destination.task.status === 'in_progress' })
    }
  }
  return { nodes: [...positions.values()], edges, cyclic, height: box.padding * 2 + Math.max(1, row) * (box.height + box.rowGap) - box.rowGap }
}

type Hop = { node: string; edge: string }
export function graphLineages(edges: DagEdge[]) {
  const ancestors = new Map<string, Hop[]>(), descendants = new Map<string, Hop[]>()
  for (const edge of edges) {
    const split = edge.id.indexOf('->')
    const from = edge.from && edge.to ? edge.from : split > 0 ? edge.id.slice(0, split) : undefined
    const to = edge.from && edge.to ? edge.to : split > 0 ? edge.id.slice(split + 2) : undefined
    if (!from || !to) continue
    ancestors.set(to, [...(ancestors.get(to) ?? []), { node: from, edge: edge.id }])
    descendants.set(from, [...(descendants.get(from) ?? []), { node: to, edge: edge.id }])
  }
  return (root: string) => {
    const nodes = new Set([root]), links = new Set<string>()
    for (const direction of [ancestors, descendants]) {
      const queue = [root], visited = new Set(queue)
      for (let position = 0; position < queue.length; position++) for (const hop of direction.get(queue[position]) ?? []) {
        links.add(hop.edge); nodes.add(hop.node)
        if (!visited.has(hop.node)) { visited.add(hop.node); queue.push(hop.node) }
      }
    }
    return { nodes, edges: links }
  }
}
