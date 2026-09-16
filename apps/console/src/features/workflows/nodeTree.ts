import type { WorkflowNodeState } from '../../shared/data/api'
import { byInstancePath } from './instancePathOrder'
import { nodeDepth } from './workflowMeta'


export interface TreeRow {
  node: WorkflowNodeState
  depth: number
  descendants: string[]
  collapsible: boolean
}

export interface SubtreeSummary {
  total: number
  byState: Record<string, number>
  dominant: string
}

const TERMINAL = new Set([
  'done', 'degraded', 'failed', 'skipped', 'no_change', 'scope_violation',
  'discarded', 'escalated', 'blocked', 'cancelled',
])

export function buildTree(nodes: WorkflowNodeState[]): TreeRow[] {
  const sorted = [...nodes].sort(byInstancePath)
  return sorted.map((node) => {
    const prefix = `${node.instance_path}.`
    const descendants = sorted
      .filter((n) => n.instance_path !== node.instance_path && n.instance_path.startsWith(prefix))
      .map((n) => n.instance_path)
    return {
      node,
      depth: nodeDepth(node.instance_path),
      descendants,
      collapsible: descendants.length > 1,
    }
  })
}

export function summarize(paths: string[], nodes: WorkflowNodeState[]): SubtreeSummary {
  const set = new Set(paths)
  const members = nodes.filter((n) => set.has(n.instance_path))
  const byState: Record<string, number> = {}
  for (const m of members) byState[m.state] = (byState[m.state] ?? 0) + 1
  const rank = (s: string) =>
    s === 'failed' || s === 'scope_violation' || s === 'blocked' ? 0
      : s === 'escalated' ? 1
      : s === 'running' || s === 'waiting' ? 2
      : s === 'degraded' ? 3
      : 4
  const dominant = Object.entries(byState)
    .sort((a, b) => b[1] - a[1] || rank(a[0]) - rank(b[0]))[0]?.[0] ?? ''
  return { total: members.length, byState, dominant }
}

export function initialCollapsed(rows: TreeRow[], nodes: WorkflowNodeState[]): Set<string> {
  const out = new Set<string>()
  for (const row of rows) {
    if (!row.collapsible) continue
    const summary = summarize(row.descendants, nodes)
    const members = nodes.filter((n) => new Set(row.descendants).has(n.instance_path))
    const allTerminal = members.every((m) => TERMINAL.has(m.state))
    const anyBad = members.some(
      (m) => m.state === 'failed' || m.state === 'scope_violation' || m.state === 'blocked',
    )
    if (allTerminal && !anyBad && summary.total > 1) out.add(row.node.instance_path)
  }
  return out
}

export function visibleRows(rows: TreeRow[], collapsed: Set<string>): TreeRow[] {
  if (collapsed.size === 0) return rows
  return rows.filter((row) => {
    for (const path of collapsed) {
      if (row.node.instance_path.startsWith(`${path}.`)) return false
    }
    return true
  })
}

export function summaryLabel(summary: SubtreeSummary): string {
  const order = ['failed', 'scope_violation', 'blocked', 'escalated', 'running', 'waiting', 'degraded']
  const entries = Object.entries(summary.byState).sort((a, b) => {
    const ia = order.indexOf(a[0])
    const ib = order.indexOf(b[0])
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)
  })
  return entries.map(([state, n]) => `${n} ${state.replace(/_/g, ' ')}`).join(' · ')
}
