import type { WorkflowContinuation, WorkflowNodeState } from '../../shared/data/api'
import type { DagEdge, DagNode, DagNodeState } from '../tasks/DagView'
import { buildTree } from './nodeTree'


export const NODE_W = 168
export const NODE_H = 44
export const COL_GAP = 56
export const ROW_GAP = 16

export const GATE_OVERLAY_H = 40

const STATE_MAP: Record<string, DagNodeState> = {
  pending: 'todo',
  ready: 'todo',
  running: 'active',
  waiting: 'awaiting',
  done: 'done',
  degraded: 'done',
  no_change: 'done',
  skipped: 'todo',
  discarded: 'todo',
  cancelled: 'todo',
  blocked: 'blocked',
  failed: 'error',
  scope_violation: 'error',
  escalated: 'error',
}

export function dagState(state: string): DagNodeState {
  return STATE_MAP[state] ?? 'todo'
}

export function isAwaitingHuman(
  node: Pick<WorkflowNodeState, 'instance_path' | 'state'>,
  continuations: Array<Pick<WorkflowContinuation, 'instance_path'> & { expired?: boolean }>,
): boolean {
  if (node.state !== 'waiting') return false
  return (continuations ?? []).some((c) => c.instance_path === node.instance_path && !c.expired)
}

export interface RunDagLayout {
  nodes: DagNode[]
  edges: DagEdge[]
  width: number
  height: number
}

export function layoutRunDag(
  nodes: WorkflowNodeState[],
  options: {
    continuations?: Array<Pick<WorkflowContinuation, 'instance_path'> & { expired?: boolean }>
    label?: (node: WorkflowNodeState) => string
  } = {},
): RunDagLayout {
  const rows = buildTree(nodes)
  if (rows.length === 0) return { nodes: [], edges: [], width: 0, height: 0 }

  const continuations = options.continuations ?? []
  const nextRow = new Map<number, number>()
  const placed = new Map<string, { x: number; y: number; depth: number }>()
  const out: DagNode[] = []

  const rawDepth = new Map<string, number>()
  for (const row of rows) rawDepth.set(row.node.instance_path, nestingOf(row.node.instance_path))
  const minDepth = Math.min(...[...rawDepth.values()])
  const depthOf = new Map<string, number>()

  for (const row of rows) {
    const depth = (rawDepth.get(row.node.instance_path) ?? 0) - minDepth
    depthOf.set(row.node.instance_path, depth)
    const slot = nextRow.get(depth) ?? 0
    nextRow.set(depth, slot + 1)
    const x = depth * (NODE_W + COL_GAP)
    const y = slot * (NODE_H + ROW_GAP)
    placed.set(row.node.instance_path, { x, y, depth })
    const awaiting = isAwaitingHuman(row.node, continuations)
    out.push({
      id: row.node.instance_path,
      x,
      y,
      w: NODE_W,
      h: NODE_H,
      state: awaiting ? 'awaiting' : dagState(row.node.state),
      content: options.label ? options.label(row.node) : row.node.node_id,
    })
  }

  const edges: DagEdge[] = []
  for (const row of rows) {
    const parentPath = parentOf(row.node.instance_path, placed)
    if (!parentPath) continue
    const from = placed.get(parentPath)
    const to = placed.get(row.node.instance_path)
    if (!from || !to) continue
    edges.push({
      id: `${parentPath}->${row.node.instance_path}`,
      from: parentPath,
      to: row.node.instance_path,
      x1: from.x + NODE_W,
      y1: from.y + NODE_H / 2,
      x2: to.x,
      y2: to.y + NODE_H / 2,
      active: row.node.state === 'running',
    })
  }

  const maxDepth = Math.max(...[...depthOf.values()])
  const maxSlot = Math.max(...[...nextRow.values()])
  const overlay = out.some((n) => n.state === 'awaiting') ? GATE_OVERLAY_H : 0
  return {
    nodes: out,
    edges,
    width: (maxDepth + 1) * NODE_W + maxDepth * COL_GAP,
    height: maxSlot * NODE_H + Math.max(0, maxSlot - 1) * ROW_GAP + overlay,
  }
}

function nestingOf(path: string): number {
  let count = 0
  for (const ch of path) if (ch === '[') count += 1
  return count
}

function parentOf(path: string, placed: Map<string, unknown>): string {
  let best = ''
  for (const candidatePath of placed.keys()) {
    if (candidatePath === path) continue
    if (!path.startsWith(candidatePath)) continue
    if (candidatePath.length > best.length) best = candidatePath
  }
  if (best) return best

  let candidate = path
  for (;;) {
    const cut = Math.max(candidate.lastIndexOf('.'), candidate.lastIndexOf('['))
    if (cut <= 0) return ''
    candidate = candidate.slice(0, cut)
    if (placed.has(candidate)) return candidate
    if (!candidate.includes('.') && !candidate.includes('[')) return ''
  }
}
