import type { TaskItem } from '../../shared/data/api'

export type DepMap = Map<string, string[]>
export interface LayeredNode { id: string; layer: number; row: number }

export function prereqIds(task: TaskItem): string[] {
  const ids: string[] = []
  for (const edge of task.dependencies ?? []) {
    if ((edge.dependency_type == null || edge.dependency_type === 'BLOCKS') && edge.depends_on_task_id) ids.push(edge.depends_on_task_id)
  }
  return ids
}
export function depMap(tasks: TaskItem[]): DepMap {
  const present = new Set(tasks.map(task => task.id))
  return new Map(tasks.map(task => [task.id, prereqIds(task).filter(id => id !== task.id && present.has(id))]))
}
export function reaches(graph: DepMap, from: string, target: string): boolean {
  const queue = [from]
  const seen = new Set(queue)
  for (let cursor = 0; cursor < queue.length; cursor++) {
    const id = queue[cursor]
    if (id === target) return true
    for (const next of graph.get(id) ?? []) if (!seen.has(next)) { seen.add(next); queue.push(next) }
  }
  return false
}
export function wouldCycle(graph: DepMap, taskId: string, candidate: string): boolean {
  return reaches(graph, candidate, taskId)
}
export function cyclicNodes(graph: DepMap): Set<string> {
  const reverse: DepMap = new Map()
  for (const [id, links] of graph) {
    if (!reverse.has(id)) reverse.set(id, [])
    for (const next of links) { const parents = reverse.get(next) ?? []; parents.push(id); reverse.set(next, parents) }
  }
  const seen = new Set<string>()
  const finished: string[] = []
  for (const root of reverse.keys()) {
    if (seen.has(root)) continue
    const stack: { id: string; expanded: boolean }[] = [{ id: root, expanded: false }]
    while (stack.length) {
      const frame = stack.pop()!
      if (frame.expanded) { finished.push(frame.id); continue }
      if (seen.has(frame.id)) continue
      seen.add(frame.id); stack.push({ ...frame, expanded: true })
      const children = graph.get(frame.id) ?? []
      for (let index = children.length - 1; index >= 0; index--) if (!seen.has(children[index])) stack.push({ id: children[index], expanded: false })
    }
  }
  const assigned = new Set<string>()
  const cycles = new Set<string>()
  for (const root of finished.reverse()) {
    if (assigned.has(root)) continue
    const component: string[] = []
    const stack = [root]
    assigned.add(root)
    while (stack.length) {
      const id = stack.pop()!
      component.push(id)
      for (const parent of reverse.get(id) ?? []) if (!assigned.has(parent)) { assigned.add(parent); stack.push(parent) }
    }
    if (component.length > 1 || graph.get(root)?.includes(root)) for (const id of component) cycles.add(id)
  }
  return cycles
}
export function layeredLayout(graph: DepMap): { nodes: Map<string, LayeredNode>; layers: number; maxRows: number } {
  const depths = new Map<string, number>()
  const active = new Set<string>()
  for (const root of graph.keys()) {
    if (depths.has(root)) continue
    const stack = [{ id: root, cursor: 0, depth: 0 }]
    active.add(root)
    while (stack.length) {
      const frame = stack[stack.length - 1]
      const links = graph.get(frame.id) ?? []
      if (frame.cursor >= links.length) {
        depths.set(frame.id, frame.depth); active.delete(frame.id); stack.pop()
        if (stack.length) stack[stack.length - 1].depth = Math.max(stack[stack.length - 1].depth, frame.depth + 1)
        continue
      }
      const next = links[frame.cursor++]
      if (depths.has(next) || active.has(next)) frame.depth = Math.max(frame.depth, (depths.get(next) ?? 0) + 1)
      else { active.add(next); stack.push({ id: next, cursor: 0, depth: 0 }) }
    }
  }
  const nodes = new Map<string, LayeredNode>()
  const counts = new Map<number, number>()
  let layers = 1, maxRows = 1
  for (const id of graph.keys()) {
    const layer = depths.get(id) ?? 0
    const row = counts.get(layer) ?? 0
    nodes.set(id, { id, layer, row }); counts.set(layer, row + 1)
    layers = Math.max(layers, layer + 1); maxRows = Math.max(maxRows, row + 1)
  }
  return { nodes, layers, maxRows }
}
