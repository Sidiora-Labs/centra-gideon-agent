import type { DagEdge, DagNode } from '../tasks/DagView'
import type { PlanDraft, PlanStep } from './planStream'

const BOX = { width: 168, height: 44, across: 48, down: 16 }
export interface PlanDagLayout { nodes: DagNode[]; edges: DagEdge[]; width: number; height: number }

export function planDepths(steps: PlanStep[]): Map<string, number> {
  const catalog = new Map(steps.map((step) => [step.id, step]))
  const depths = new Map<string, number>()
  type Visit = { id: string; deps: string[]; cursor: number; depth: number }
  for (const step of steps) {
    if (depths.has(step.id)) continue
    const active = new Set<string>()
    const stack: Visit[] = []
    const enter = (id: string) => {
      active.add(id)
      stack.push({ id, deps: catalog.get(id)?.depends_on ?? [], cursor: 0, depth: 0 })
    }
    enter(step.id)
    while (stack.length) {
      const visit = stack[stack.length - 1]
      if (visit.cursor >= visit.deps.length) {
        depths.set(visit.id, visit.depth)
        active.delete(visit.id)
        stack.pop()
        const parent = stack[stack.length - 1]
        if (parent) parent.depth = Math.max(parent.depth, visit.depth + 1)
        continue
      }
      const dependency = visit.deps[visit.cursor++]
      if (dependency === visit.id || !catalog.has(dependency)) continue
      const depth = depths.get(dependency)
      if (depth !== undefined || active.has(dependency) || stack.length >= steps.length) {
        visit.depth = Math.max(visit.depth, (depth ?? 0) + 1)
      } else enter(dependency)
    }
  }
  return depths
}

export function layoutPlanDag(draft: PlanDraft, label: (step: PlanStep) => string = (step) => step.label ?? step.id): PlanDagLayout {
  if (!draft.steps.length) return { nodes: [], edges: [], width: 0, height: 0 }
  const declared = draft.steps.some((step) => Boolean(step.depends_on?.length))
  const columns = declared ? planDepths(draft.steps) : new Map(draft.steps.map((step, index) => [step.id, index]))
  const occupied = new Map<number, number>()
  const nodes: DagNode[] = draft.steps.map((step) => {
    const column = columns.get(step.id) ?? 0
    const row = occupied.get(column) ?? 0
    occupied.set(column, row + 1)
    return { id: step.id, x: column * (BOX.width + BOX.across), y: row * (BOX.height + BOX.down), w: BOX.width, h: BOX.height, state: step.pending ? 'active' : 'todo', content: label(step) }
  })
  const positions = new Map(nodes.map((node) => [node.id, node]))
  const links = draft.steps.flatMap((step, index) => {
    const before = declared ? step.depends_on ?? [] : index ? [draft.steps[index - 1].id] : []
    return before.filter((id) => (!declared || id !== step.id) && positions.has(id)).map((id) => [id, step.id] as const)
  })
  const edges: DagEdge[] = links.map(([from, to]) => {
    const origin = positions.get(from)!
    const destination = positions.get(to)!
    return { id: `${from}->${to}`, from, to, x1: origin.x + BOX.width, y1: origin.y + BOX.height / 2, x2: destination.x, y2: destination.y + BOX.height / 2 }
  })
  return {
    nodes, edges,
    width: Math.max(...nodes.map((node) => node.x)) + BOX.width,
    height: Math.max(...nodes.map((node) => node.y)) + BOX.height,
  }
}
