import type { TaskItem } from '../../shared/data/api'
import type { DagEdge } from './DagView'
import { cyclicNodes, depMap, layeredLayout } from './dag'

export const GRAPH_BOX = { width: 210, height: 58, rowGap: 64, columnGap: 28, padding: 24, radius: 12 }

export function taskTagOptions(tasks: TaskItem[]) {
  const counts = new Map<string, number>()
  for (const task of tasks) for (const tag of new Set((task.labels ?? []).map(label => label.trim()).filter(Boolean))) counts.set(tag, (counts.get(tag) ?? 0) + 1)
  return [...counts].sort(([left], [right]) => left.localeCompare(right)).map(([key, count]) => ({ key, label: key, count }))
}

export function filterTasksByTag(tasks: TaskItem[], tag: string) {
  return tag ? tasks.filter(task => (task.labels ?? []).some(label => label.trim() === tag)) : tasks
}

export function taskRowLocked(task: TaskItem) { return task.provider === 'project' }

export function preserveLockedTaskRows(rows: TaskItem[], proposal: TaskItem[]) {
  const movable = rows.filter(task => !taskRowLocked(task))
  const available = new Map(movable.map(task => [task.id, task]))
  const ordered: TaskItem[] = []
  for (const task of [...proposal, ...movable]) {
    const current = available.get(task.id)
    if (!current) continue
    ordered.push(current)
    available.delete(task.id)
  }
  let position = 0
  return rows.map(task => taskRowLocked(task) ? task : ordered[position++])
}

export function orderTaskRows(rows: TaskItem[], preferred: string[] | null = null) {
  const source = rows.map((task, index) => ({ task, index }))
    .sort((left, right) => (left.task.order ?? left.index) - (right.task.order ?? right.index) || left.index - right.index)
    .map(entry => entry.task)
  if (!preferred?.length) return source
  const available = new Map(source.map(task => [task.id, task]))
  const ordered: TaskItem[] = []
  for (const id of preferred) {
    const task = available.get(id)
    if (!task) continue
    ordered.push(task)
    available.delete(id)
  }
  return [...ordered, ...available.values()]
}

export type TaskNoMatchCause = 'search' | 'tag' | 'scope' | 'filters'
export function taskNoMatchCause(input: {
  query: string; searchMatches: number | null; scope: string; tag: string
  status: string; list: string; mine: boolean
}): TaskNoMatchCause {
  if (input.query && input.searchMatches === 0) return 'search'
  const active = [!!input.query, !!input.scope, !!input.tag, input.status !== 'all', !!input.list, input.mine].filter(Boolean).length
  if (active > 1) return 'filters'
  if (input.tag) return 'tag'
  if (input.scope) return 'scope'
  return input.query ? 'search' : 'filters'
}

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
